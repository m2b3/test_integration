// ===========================================================================
//
//                            PUBLIC DOMAIN NOTICE
//            National Center for Biotechnology Information (NCBI)
//
//  This software/database is a "United States Government Work" under the
//  terms of the United States Copyright Act. It was written as part of
//  the author's official duties as a United States Government employee and
//  thus cannot be copyrighted. This software/database is freely available
//  to the public for use. The National Library of Medicine and the U.S.
//  Government do not place any restriction on its use or reproduction.
//  We would, however, appreciate having the NCBI and the author cited in
//  any work or product based on this material.
//
//  Although all reasonable efforts have been taken to ensure the accuracy
//  and reliability of the software and data, the NLM and the U.S.
//  Government do not and cannot warrant the performance or results that
//  may be obtained by using this software or data. The NLM and the U.S.
//  Government disclaim all warranties, express or implied, including
//  warranties of performance, merchantability or fitness for any particular
//  purpose.
//
// ===========================================================================
//
// File Name:  cache.go
//
// Author:  Jonathan Kans
//
// ==========================================================================

package eutils

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"slices"
	"strconv"
	"strings"
	"sync"
)

// Entrez Direct local archive files each store up to 10,000 individually-gzipped
// source data records. The file naming convention indicates the range of integer
// identifiers assigned to an archive. For example, PMID 2539356 is zero-padded to
// 0002539356, giving "000253" for the archive name and "/00/02/000253.archive" for
// the database file path. That file will contain all live PubmedArticle XML records
// with PMIDs ranging from 2530000 through 2539999, inclusive.

// Preceding the compressed records is a binary array of cumulative lengths for the
// set of records that may be present in that file. This is read into a memory index,
// which begins with an additional slot that is initialized to 0. The index table now
// points to the relative start positions of each record. Incrementing each number by
// 80,000 (10,000 entries of 8 bytes each) to advance past the index table makes the
// values absolute offsets to the start of each record on the disk file.

// (For convenience and efficiency, the cumulative length calculation actually starts
// with 80000 to pre-increment the values that are stored in the archive file.)

// PMID 2539356 corresponds to (zero-based) position "9356" in the "000253.archive"
// offset table. The compressed XML record starts at "position[9356]" on the disk, and
// its length is calculated by "position[9357]" - "position[9356]". With an in-memory
// index of 10,001 slots, this allows record "9999" to access "position[10000]" without
// requiring any special code to prevent this edge case from causing a computer crash.

// When an update to an existing archive occurs (such as with PubMed once it starts
// processing the daily "updatefiles" ftp folder), the ".archive" suffix is changed to
// ".records", and new compressed records are appended to the file without updating the
// internal index table.

// At the end of a daily record update, any ".records" suffix is changed to ".staging",
// and these are reprocessed into new ".archive" files, which automatically keeps the
// last version of each record and builds a fresh, up-to-date index table. One this is
// done, the ".staging" files are deleted.

// To obtain all records in an archive file with a Unix terminal or shell script, pipe
// existing archive files through "dd ibs=8000 skip=10 2> /dev/null" to skip past the
// cumulative length array, and then pipe through "gunzip -c" to uncompress the set of
// component records and stream the results to stdout.

// To do this in a Go program, call StreamArchiveComponents or StreamArchiveContents.

// OPEN ARCHIVE

const archiveSuffix = ".archive"
const recordsSuffix = ".records"

// getArchiveFilePath builds a file path and looks for an existing file, first with
// the ".archive" suffix, then with the ".records" suffix. If one of these files is
// present, it returns the path, the suffix, and true. If not, it returns the path,
// ".archive" as the suffix for a new file to create, and false.
func getArchiveFilePath(archiveBase, dbfile string, makeDirs bool) (string, string, bool) {

	if archiveBase == "" || dbfile == "" {
		return "", "", false
	}

	pos := strings.Index(dbfile, ".")
	if pos >= 0 {
		fmt.Fprintf(os.Stderr, "Archive name %s has unexpected suffix", dbfile)
		// remove any extraneous suffix
		dbfile = dbfile[:pos]
		if dbfile == "" {
			return "", "", false
		}
	}

	baseUID := dbfile + "0000"
	dir, file := ArchiveTrie(baseUID)
	if dir == "" || file == "" {
		fmt.Fprintf(os.Stderr, "Unable to calculate directory or file from %s", baseUID)
		return "", "", false
	}

	dpath := filepath.Join(archiveBase, dir)
	if dpath == "" {
		return "", "", false
	}

	if makeDirs {
		// create any missing subfolders to the archive file for the current range of records
		err := os.MkdirAll(dpath, os.ModePerm)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Unable to make directories %s: %s\n", dpath, err.Error())
			return "", "", false
		}
	}

	fpath := filepath.Join(dpath, file)
	if fpath == "" {
		return "", "", false
	}

	doesFileExist := func(fpath, suffix string) bool {

		// check to see if file is already present
		info, err := os.Stat(fpath + suffix)
		if err != nil {
			if os.IsNotExist(err) {
				return false
			}
			fmt.Fprintf(os.Stderr, "File test error on %s: %s\n", fpath+suffix, err.Error())
			return false
		}

		if info.Size() < 1 {
			// remove if file exists but is is empty
			err := os.Remove(fpath + archiveSuffix)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Unable to remove empty file %s: %s\n", fpath+suffix, err.Error())
			}
			return false
		}

		// return file exists
		return true
	}

	if doesFileExist(fpath, archiveSuffix) {
		return fpath, archiveSuffix, true
	}

	if doesFileExist(fpath, recordsSuffix) {
		return fpath, recordsSuffix, true
	}

	// return path and suffix for new file to create
	return fpath, archiveSuffix, false
}

// fetchArchivePositions returns an array of record offsets in an archive file, obtained by
// reading the cumulative lengths table at the beginning of the file. To retrieve the bytes
// for compressed record i, start is positions[i], length is positions[i+1] - positions[i].
func fetchArchivePositions(rfl *os.File) ([10001]int64, bool) {

	// Use of 10001 positions in memory array allows item 9999 to access the next position
	// and calculate the length of the record without causing an out-of-range crash.

	var positions [10001]int64

	if rfl == nil {
		return positions, false
	}

	_, err := rfl.Seek(int64(0), io.SeekStart)
	if err != nil {
		fmt.Fprintf(os.Stderr, "fetchArchivePositions rfl.Seek failed: %s\n", err.Error())
		return positions, false
	}

	// read cumulative lengths (pre-offset in the archive file) into (zero-based)
	// positions 1 through 10000 of the array (just beyond position 0)
	err = binary.Read(rfl, binary.LittleEndian, positions[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "fetchArchivePositions binary.Read failed to read cumulative lengths table: %s\n", err.Error())
		return positions, false
	}

	// offset position 0, for record 0000, to skip the archive's cumulative lengths table
	positions[0] = 80000

	return positions, true
}

func fetchArchiveRecord(rfl *os.File, positions [10001]int64, recordNumber int) ([]byte, bool) {

	if rfl == nil {
		return nil, false
	}

	if len(positions) < 10001 {
		return nil, false
	}

	// archive stores records (modulo 0000 through 9999 inclusive) in a specific range of IDs
	if recordNumber < 0 || recordNumber > 9999 {
		return nil, false
	}

	// stop for modulo 9999 is in last slot of 10001 int8 array
	start := positions[recordNumber]
	stop := positions[recordNumber+1]

	size := stop - start
	if size < 1 {
		// record UID not indexed, but keep going
		return nil, true
	}

	bytes := make([]byte, size)
	if bytes == nil || len(bytes) < 1 {
		return nil, false
	}

	_, err := rfl.Seek(int64(start), io.SeekStart)
	if err != nil {
		fmt.Fprintf(os.Stderr, "fetchArchiveRecord rfl.Seek failed: %s\n", err.Error())
		return nil, false
	}

	err = binary.Read(rfl, binary.LittleEndian, bytes)
	if err != nil {
		fmt.Fprintf(os.Stderr, "fetchArchiveRecord binary.Read failed: %s\n", err.Error())
		return nil, false
	}

	return bytes, true
}

func openArchiveForReading(archiveBase, dbfile string) *os.File {

	if archiveBase == "" || dbfile == "" {
		return nil
	}

	pos := strings.Index(dbfile, ".")
	if pos >= 0 {
		// remove any suffix
		dbfile = dbfile[:pos]
	}

	fpath, suffix, fileExists := getArchiveFilePath(archiveBase, dbfile, false)

	if !fileExists {
		return nil
	}

	if suffix == recordsSuffix {
		fmt.Fprintf(os.Stderr, "Cannot use '.records' file, must first repopulate '.archive' file")
		return nil
	}

	dfl, err := os.Open(fpath + suffix)

	if err != nil {
		msg := err.Error()
		if !strings.HasSuffix(msg, "no such file or directory") &&
			!strings.HasSuffix(msg, "cannot find the path specified.") {
			fmt.Fprintf(os.Stderr, "os.Open failure for target '%s': %s\n", fpath+suffix, msg)
		}
		return nil
	}

	return dfl
}

// GzipString allows separate compression of XML components
func GzipString(str string, putNewlineAtEnd bool) []byte {

	if str == "" {
		return nil
	}

	var buf bytes.Buffer

	gzWriter, err := gzip.NewWriterLevel(&buf, gzip.DefaultCompression)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s\n", err.Error())
		return nil
	}

	gzWriter.Write([]byte(str))

	if putNewlineAtEnd {
		if !strings.HasSuffix(str, "\n") {
			gzWriter.Write([]byte("\n"))
		}
	}

	gzWriter.Flush()
	gzWriter.Close()

	return buf.Bytes()
}

// UIDReader sends identifiers and their numeric orders down a channel.
func UIDReader(in io.Reader) <-chan XMLRecord {

	if in == nil {
		return nil
	}

	out := make(chan XMLRecord, chanDepth)
	if out == nil {
		DisplayError("Unable to create uid reader channel")
		os.Exit(1)
	}

	go func(in io.Reader, out chan<- XMLRecord) {

		// close channel when all records have been processed
		defer close(out)

		scanr := bufio.NewScanner(in)

		idx := 0
		for scanr.Scan() {

			// read lines of identifiers
			file := scanr.Text()
			idx++

			pos := strings.Index(file, ".")
			if pos >= 0 {
				// remove version suffix
				file = file[:pos]
			}

			out <- XMLRecord{Index: idx, Text: file}
		}
	}(in, out)

	return out
}

// UIDStreamer can read from the standard input stream (os.Stdin), or
// from a file (*os.File), or from a string literal or variable with:
// ChanToReader(StringToChan(string-containing-comma-separated-UIDs))
func UIDStreamer(in io.Reader) <-chan string {

	if in == nil {
		fmt.Fprintf(os.Stderr, "No input to UIDStreamer")
		return nil
	}

	out := make(chan string, chanDepth)
	if out == nil {
		fmt.Fprintf(os.Stderr, "Unable to create uid streamer channel")
		return nil
	}

	go func(in io.Reader, out chan<- string) {

		// close channel when all records have been processed
		defer close(out)

		scanr := bufio.NewScanner(in)

		for scanr.Scan() {

			// read lines of identifiers
			line := scanr.Text()

			if line == "" {
				continue
			}

			items := strings.SplitSeq(line, ",")
			for id := range items {
				pos := strings.Index(id, ".")
				if pos >= 0 {
					// remove version suffix
					id = id[:pos]
				}

				out <- id
			}
		}
	}(in, out)

	return out
}

// WRITE ARCHIVE

// WriteArchive reads XML (from stdin or a channel) and generates EDirect local archive files.
func WriteArchive(db, parent, index, dlete string, inp <-chan XMLRecord) <-chan string {

	type recordByIDMap map[string][]byte
	type deleteByIDMap map[string]bool

	type recordGroup struct {
		file string
		data recordByIDMap
		drop deleteByIDMap
	}

	if db == "" {
		fmt.Fprintf(os.Stderr, "Database not specified")
		return nil
	}

	if parent == "" {
		fmt.Fprintf(os.Stderr, "Parent record name not specified")
		return nil
	}

	if index == "" {
		fmt.Fprintf(os.Stderr, "Record index object not specified")
		return nil
	}

	// expand backslash-n to newline character
	dlete = ConvertSlash(dlete)

	if inp == nil {
		// if input channel argument is nil, use Stdin for input
		rdr := CreateXMLStreamer(os.Stdin, nil)

		inp = CreateXMLProducer(parent, "", false, rdr)
	}

	// e.g., "MedlineCitation/PMID"
	find := ParseIndex(index)
	if find == nil {
		fmt.Fprintf(os.Stderr, "Unable to parse index field description")
		return nil
	}

	// obtain paths from environment variable(s) or configuration file
	pths := ResolveArchivePaths(db)
	if pths == nil {
		fmt.Fprintf(os.Stderr, "Unable to get local archive configuration paths")
		return nil
	}

	archiveBase, ok := pths.GetLocalPath("Archive")
	if archiveBase == "" {
		fmt.Fprintf(os.Stderr, "Unable to get local archive path")
		return nil
	}

	invertBase, ok := pths.GetLocalPath("Invert")
	if invertBase == "" {
		fmt.Fprintf(os.Stderr, "Unable to get local invert path")
		return nil
	}

	if !ok {
		fmt.Fprintf(os.Stderr, "Local archive is not mounted")
		return nil
	}

	identAndGzip := func(index string, find *XMLFind, inp <-chan XMLRecord) <-chan XMLRecord {

		if index == "" || inp == nil {
			return nil
		}

		out := make(chan XMLRecord, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create gzipper channel")
			return nil
		}

		idAndGzip := func(wg *sync.WaitGroup, find *XMLFind, inp <-chan XMLRecord, out chan<- XMLRecord) {

			defer wg.Done()

			for ext := range inp {

				idx := ext.Index
				text := ext.Text

				if text == "" {
					out <- XMLRecord{Index: idx}
					continue
				}

				id := FindIdentifier(text[:], "", find)

				// individually gzip each record
				bytes := GzipString(text[:], true)

				// if deletion pattern (e.g., "<PubmedArticle><DELETE>") is at head of record
				if dlete != "" && strings.HasPrefix(text[:], dlete) {
					// pass the compressed version of the record, plus the original XML text string
					out <- XMLRecord{Index: idx, Ident: id, Data: bytes, Text: text}
					continue
				}

				// if not a deletion, only pass the compressed version
				out <- XMLRecord{Index: idx, Ident: id, Data: bytes}
			}
		}

		var wg sync.WaitGroup

		// launch multiple identifier/gzipper goroutines
		for range numServe {
			wg.Add(1)
			go idAndGzip(&wg, find, inp, out)
		}

		// launch separate anonymous goroutine to wait until all identifier/gzippers are done
		go func() {
			wg.Wait()
			close(out)
		}()

		return out
	}

	groupByRecord := func(inp <-chan XMLRecord) <-chan recordGroup {

		out := make(chan recordGroup, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create grouper channel")
			return nil
		}

		currentDbFile := ""

		go func(inp <-chan XMLRecord, out chan<- recordGroup) {

			defer close(out)

			recordByID := make(recordByIDMap)
			if recordByID == nil {
				fmt.Fprintf(os.Stderr, "Unable to create ID to record map")
				return
			}

			deleteByID := make(deleteByIDMap)
			if deleteByID == nil {
				fmt.Fprintf(os.Stderr, "Unable to create ID to delete map")
				return
			}

			for ext := range inp {

				idnt := ext.Ident
				bytes := ext.Data
				text := ext.Text

				if idnt == "" {
					return
				}
				// remove default version suffix
				idnt = strings.TrimSuffix(idnt, ".1")

				padded := PadNumericID(idnt)
				// remove the last 4 digits to get appropriate file name
				dbfile := padded[:6]
				id := padded

				if dbfile != currentDbFile && currentDbFile != "" {

					// send map of compressed XML for current group of 10,000 records
					out <- recordGroup{file: currentDbFile, data: recordByID, drop: deleteByID}

					// set maps to nil to disconnect from underlying data, allow garbage collection
					recordByID = nil
					deleteByID = nil

					// then recreate new map instances for next set of records
					recordByID = make(recordByIDMap)
					if recordByID == nil {
						fmt.Fprintf(os.Stderr, "Unable to create ID to record map")
						return
					}

					deleteByID = make(deleteByIDMap)
					if deleteByID == nil {
						fmt.Fprintf(os.Stderr, "Unable to create ID to delete map")
						return
					}
				}

				currentDbFile = dbfile

				if text != "" && dlete != "" && strings.HasPrefix(text[:], dlete) {
					// this flag needed when reconstructing archive
					deleteByID[id] = true
				}

				// subsequent records with same ID overwrite earlier entries
				recordByID[id] = bytes
			}

			if len(recordByID) > 0 {
				out <- recordGroup{file: currentDbFile, data: recordByID, drop: deleteByID}
			}
		}(inp, out)

		return out
	}

	writeFromMap := func(archiveBase, dbfile string, recordByID recordByIDMap, deleteByID deleteByIDMap) {

		if recordByID == nil || len(recordByID) < 1 {
			return
		}

		fpath, suffix, fileExists := getArchiveFilePath(archiveBase, dbfile, true)

		if fpath == "" {
			fmt.Fprintf(os.Stderr, "Unable to find path for archive file %s\n", dbfile)
			return
		}

		keys := slices.SortedFunc(maps.Keys(recordByID), CompareAlphaOrNumericKeys)

		if keys == nil {
			fmt.Fprintf(os.Stderr, "Unable to sort record keys for archive file %s\n", dbfile)
			return
		}

		// if indexed archive already exists, switch to append mode without reindexing
		if fileExists {

			// on first update, rename file to use recordsSuffix
			if suffix == archiveSuffix {
				err := os.Rename(fpath+archiveSuffix, fpath+recordsSuffix)
				if err != nil {
					fmt.Fprintf(os.Stderr, "Unable to rename %s to %s: %s\n",
						fpath+archiveSuffix, fpath+recordsSuffix, err.Error())
					return
				}
			}

			// open renamed file for appending
			dfl, err := os.OpenFile(fpath+recordsSuffix, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0644)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Error trying to open renamed %s for appending: %s\n",
					fpath+recordsSuffix, err.Error())
				return
			}

			defer dfl.Close()

			// append new data records, including deletion records, to renamed archive
			for _, id := range keys {

				bytes := recordByID[id]
				if bytes == nil {
					continue
				}

				dfl.Write(bytes)
			}

			// subsequent resolution step will decompress and reprocess to create
			// an updated indexed archive after all new records are distributed
			return
		}

		// otherwise create new indexed archive file
		dfl, err := os.Create(fpath + archiveSuffix)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Unable to create archive file %s: %s\n",
				fpath+archiveSuffix, err.Error())
			return
		}

		defer dfl.Close()

		// calculate and write cumulative lengths array at head of new archive file

		var lengths [10000]int64

		for _, id := range keys {

			padded := PadNumericID(id)

			// modulo is between 0000 and 9999 inclusive
			modulo := padded[6:]

			i, err := strconv.Atoi(modulo)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Unable to convert string to integer: %s", id)
			}

			if deleteByID[id] {
				// continue if record is marked for deletion
				continue
			}

			bytes := recordByID[id]
			if bytes == nil {
				// continue if compression failed
				continue
			}

			lengths[i] = int64(len(bytes))
		}

		// note 10000 cumulative lengths, reading will use array of 10001 positions, setting first after
		var cumulative [10000]int64

		// initialize file offset for records to just beyond 10,000 element x 8-byte integer index table
		currentPos := int64(80000)

		for i := range cumulative {

			currentPos += lengths[i]
			cumulative[i] = currentPos
		}

		binary.Write(dfl, binary.LittleEndian, &cumulative)

		// append new data records to archive
		for _, id := range keys {

			if deleteByID[id] {
				continue
			}

			bytes := recordByID[id]
			if bytes == nil {
				continue
			}

			dfl.Write(bytes)
		}
	}

	writeOneArchive := func(archiveBase string, inp <-chan recordGroup) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create writer channel")
			return nil
		}

		writer := func(wg *sync.WaitGroup, inp <-chan recordGroup, out chan<- string) {

			defer wg.Done()

			count := 0

			for rg := range inp {

				writeFromMap(archiveBase, rg.file, rg.data, rg.drop)

				out <- rg.file

				count++
				if count > 999 {
					count = 0

					runtime.GC()
					runtime.Gosched()
					debug.FreeOSMemory()
				}
			}
		}

		var wg sync.WaitGroup

		// launch multiple writer goroutines
		for range numServe {
			wg.Add(1)
			go writer(&wg, inp, out)
		}

		// launch separate anonymous goroutine to wait until all writers are done
		go func() {
			wg.Wait()
			close(out)
		}()

		return out
	}

	clearStaleInvertFiles := func(invertBase string, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create clearer channel")
			return nil
		}

		go func(inp <-chan string, out chan<- string) {

			defer close(out)

			// maps to track inverted index files that were deleted
			deletedInv := make(map[string]bool)

			for dbfile := range inp {

				dbfile := strings.TrimSuffix(dbfile, archiveSuffix)

				id := dbfile + "0000"

				vdir, inv := InvertTrie(id)
				if vdir == "" || inv == "" {
					continue
				}

				vpath := filepath.Join(invertBase, vdir, inv+".inv.gz")
				deletedInv[vpath] = true

				out <- dbfile
			}

			// read (uniqued) map
			for str := range deletedInv {

				// delete stale inverted index files
				os.Remove(str)
			}
		}(inp, out)

		return out
	}

	out := make(chan string, chanDepth)
	if out == nil {
		fmt.Fprintf(os.Stderr, "Unable to create stasher channel")
		return nil
	}

	gzpq := identAndGzip(index, find, inp)
	unsq := CreateXMLUnshuffler(gzpq)
	graq := groupByRecord(unsq)
	wtaq := writeOneArchive(archiveBase, graq)
	ciiq := clearStaleInvertFiles(invertBase, wtaq)

	if inp == nil || gzpq == nil || unsq == nil || graq == nil || wtaq == nil || ciiq == nil {
		fmt.Fprintf(os.Stderr, "Unable to create archiver stasher")
		return nil
	}

	return ciiq
}

// READ ARCHIVE

func gunzipOneRecord(data []byte) string {

	if data == nil || len(data) < 1 {
		return ""
	}

	rdr := bytes.NewReader(data)
	if rdr == nil {
		return ""
	}

	brd := bufio.NewReader(rdr)
	if brd == nil {
		return ""
	}

	zpr, err := gzip.NewReader(brd)
	if err != nil {
		return ""
	}
	defer zpr.Close()

	byt, err := io.ReadAll(zpr)
	if err != nil {
		return ""
	}

	str := string(byt)
	if str == "" {
		return ""
	}

	if !strings.HasSuffix(str, "\n") {
		str += "\n"
	}

	return str
}

// StreamArchiveComponents reads an open archive file, compiled equivalent to:
// "dd ibs=8000 skip=10 2> /dev/null | gunzip -c"
func StreamArchiveComponents(in io.Reader) <-chan string {

	if in == nil {
		in = os.Stdin
	}

	out := make(chan string, chanDepth)
	if out == nil {
		fmt.Fprintf(os.Stderr, "Unable to create archive component streamer channel")
		return nil
	}

	go func(in io.Reader, out chan<- string) {

		defer close(out)

		// skip index of 10,000 int64 file offsets
		_, err := io.CopyN(io.Discard, in, int64(80000))
		if err != nil && err != io.EOF {
			fmt.Fprintf(os.Stderr, "Unable to read past archvie index")
			return
		}

		brd := bufio.NewReader(in)
		if brd == nil {
			fmt.Fprintf(os.Stderr, "Unable to create buffered reader")
			return
		}

		zpr, err := gzip.NewReader(brd)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Unable to create decompressor: %s", err.Error())
			return
		}

		// close decompressor when all records have been processed
		defer zpr.Close()

		// code based on Go best practices advice, and borrowed from xmlReader
		const bufsize = 4096

		buffer := make([]byte, bufsize)

		for {
			n, err := zpr.Read(buffer)

			if err != nil {
				if err != io.EOF {
					// real error.
					fmt.Fprintf(os.Stderr, "io.Reader failure: err.Error: %s", err.Error())
					// ignore bytes - non-conforming implementations of io.Reader may
					// return mangled data on non-EOF errors
					return
				}
				// end of file.
				if n == 0 {
					return
				}
			}
			if n < 0 {
				// reality check - non-conforming implementations of io.Reader may return -1
				fmt.Fprintf(os.Stderr, "io.Reader returned negative count %d", n)
				return
			}

			out <- string(buffer[:n])
		}
	}(in, out)

	return out
}

// StreamArchiveRecords reads identifiers and returns compressed XML records from a local archive.
func StreamArchiveRecords(db string, inp <-chan string) <-chan []byte {

	if db == "" {
		fmt.Fprintf(os.Stderr, "Database not specified for archive reader")
		return nil
	}

	if inp == nil {
		fmt.Fprintf(os.Stderr, "No input channel for archive reader")
		return nil
	}

	// obtain paths from environment variable(s) or configuration file
	pths := ResolveArchivePaths(db)
	if pths == nil {
		fmt.Fprintf(os.Stderr, "Unable to get local archive configuration paths")
		return nil
	}

	archiveBase, ok := pths.GetLocalPath("Archive")
	if archiveBase == "" {
		fmt.Fprintf(os.Stderr, "Unable to get local archive path")
		return nil
	}

	if !ok {
		fmt.Fprintf(os.Stderr, "Local archive is not mounted")
		return nil
	}

	groupByIdentifier := func(inp <-chan string) <-chan []string {

		out := make(chan []string, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create streamer channel")
			return nil
		}

		currentDbFile := ""

		go func(inp <-chan string, out chan<- []string) {

			defer close(out)

			arry := make([]string, 0)

			if arry == nil {
				fmt.Fprintf(os.Stderr, "Unable to create ID array")
				return
			}

			for id := range inp {

				if id == "" {
					return
				}
				// remove default version suffix
				id = strings.TrimSuffix(id, ".1")

				padded := PadNumericID(id)
				// trim the last 4 digits
				dbfile := padded[:6]

				if dbfile != currentDbFile && currentDbFile != "" {

					out <- arry

					// set array to nil to disconnect from underlying data, allow garbage collection
					arry = nil

					// then recreate a new instance for next set of records
					arry = make([]string, 0)

					if arry == nil {
						fmt.Fprintf(os.Stderr, "Unable to create ID array")
						return
					}
				}

				currentDbFile = dbfile

				arry = append(arry, id)
			}

			if len(arry) > 0 {
				out <- arry
			}
		}(inp, out)

		return out
	}

	readFromIDs := func(archiveBase string, ids []string, out chan<- []byte) {

		if len(ids) < 1 || out == nil {
			return
		}

		// IDs are grouped by archive file, so first can be used to reconstruct it
		id := ids[0]
		id = strings.TrimSuffix(id, ".1")

		padded := PadNumericID(id)
		dbfile := padded[:6]

		dfl := openArchiveForReading(archiveBase, dbfile)
		if dfl == nil {
			// no records in this range exist, do not report error
			return
		}

		defer dfl.Close()

		positions, ok := fetchArchivePositions(dfl)
		if !ok {
			fmt.Fprintf(os.Stderr, "Unable to fetch archive position table %s\n", dbfile)
			return
		}

		for _, id := range ids {

			padded := PadNumericID(id)

			// modulo is between 0000 and 9999 inclusive
			modulo := padded[6:]

			i, err := strconv.Atoi(modulo)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Unable to convert string to integer: %s", id)
			}

			bytes, ok := fetchArchiveRecord(dfl, positions, i)
			if !ok {
				fmt.Fprintf(os.Stderr, "Unable to fetch archive %s, id %s, record %d\n", dbfile, id, i)
				continue
			}

			out <- bytes
		}
	}

	fetchRecords := func(archiveBase string, inp <-chan []string) <-chan []byte {

		out := make(chan []byte, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create fetcher channel")
			return nil
		}

		go func(inp <-chan []string, out chan<- []byte) {

			defer close(out)

			for uids := range inp {
				readFromIDs(archiveBase, uids, out)
			}
		}(inp, out)

		return out
	}

	grpq := groupByIdentifier(inp)

	ftcq := fetchRecords(archiveBase, grpq)

	if grpq == nil || ftcq == nil {
		fmt.Fprintf(os.Stderr, "Unable to create archive streamer")
		return nil
	}

	return ftcq
}

// ReadArchiveRecords reads identifiers and returns uncompressed XML records from a local archive.
func ReadArchiveRecords(db string, turbo bool, inp <-chan string) <-chan string {

	wrapCompressedRecords := func(inp <-chan []byte) <-chan XMLRecord {

		if inp == nil {
			return nil
		}

		out := make(chan XMLRecord, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create wrapper channel")
			return nil
		}

		go func(inp <-chan []byte, out chan<- XMLRecord) {

			defer close(out)

			rec := 0

			for uids := range inp {
				rec++
				out <- XMLRecord{Index: rec, Data: uids}
			}
		}(inp, out)

		return out
	}

	unzipRecords := func(inp <-chan XMLRecord) <-chan XMLRecord {

		if inp == nil {
			return nil
		}

		out := make(chan XMLRecord, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create unzip channel")
			return nil
		}

		unGzip := func(wg *sync.WaitGroup, inp <-chan XMLRecord, out chan<- XMLRecord) {

			defer wg.Done()

			for ext := range inp {

				idx := ext.Index
				bytes := ext.Data

				if bytes == nil || len(bytes) < 1 {
					out <- XMLRecord{Index: idx}
					continue
				}

				text := gunzipOneRecord(bytes[:])

				// include only the uncompressed version
				out <- XMLRecord{Index: idx, Text: text}
			}
		}

		var wg sync.WaitGroup

		// launch multiple unzip goroutines
		for range numServe {
			wg.Add(1)
			go unGzip(&wg, inp, out)
		}

		// launch separate anonymous goroutine to wait until all unzippers are done
		go func() {
			wg.Wait()
			close(out)
		}()

		return out
	}

	extractText := func(turbo bool, inp <-chan XMLRecord) <-chan string {

		if inp == nil {
			return nil
		}

		out := make(chan string, chanDepth)
		if out == nil {
			fmt.Fprintf(os.Stderr, "Unable to create extractor channel")
			return nil
		}

		go func(inp <-chan XMLRecord, out chan<- string) {

			defer close(out)

			for ext := range inp {

				text := ext.Text

				if turbo {
					out <- "<NEXT_RECORD_SIZE>" + strconv.Itoa(len(text[:])) + "</NEXT_RECORD_SIZE>\n"
				}

				out <- text[:]
			}
		}(inp, out)

		return out
	}

	// call StreamArchiveRecords as first step
	strq := StreamArchiveRecords(db, inp)

	wrpq := wrapCompressedRecords(strq)

	unzq := unzipRecords(wrpq)

	unsq := CreateXMLUnshuffler(unzq)

	xtrq := extractText(turbo, unsq)

	if strq == nil || wrpq == nil || unzq == nil || unsq == nil || xtrq == nil {
		return nil
	}

	return xtrq
}

// streamArchiveData opens a set of local archive files and streams the compressed XML records.
func streamArchiveData(archiveBase string, inp <-chan string) <-chan XMLRecord {

	if archiveBase == "" {
		fmt.Fprintf(os.Stderr, "Archive base path not specified for archive content streamer")
		return nil
	}

	if inp == nil {
		fmt.Fprintf(os.Stderr, "Channel of archive names not specified for archive content streamer")
		return nil
	}

	out := make(chan XMLRecord, chanDepth)
	if out == nil {
		fmt.Fprintf(os.Stderr, "Unable to archive content channel")
		return nil
	}

	idx := 0

	streamOneArchive := func(archiveBase, dbfile string, out chan<- XMLRecord) {

		rfl := openArchiveForReading(archiveBase, dbfile)
		if rfl == nil {
			fmt.Fprintf(os.Stderr, "Unable to open archive file %s\n", dbfile)
			return
		}

		defer rfl.Close()

		positions, ok := fetchArchivePositions(rfl)
		if !ok {
			fmt.Fprintf(os.Stderr, "Unable to fetch archive position table %s\n", dbfile)
			return
		}

		for i := range 10000 {

			bytes, ok := fetchArchiveRecord(rfl, positions, i)
			if !ok {
				fmt.Fprintf(os.Stderr, "Unable to fetch archive %s record %d\n", dbfile, i)
				continue
			}

			if len(bytes) < 1 {
				// record UID not indexed, keep going
				continue
			}

			idx++
			val := strconv.Itoa(i)
			padded := PadNumericID(val)
			id := dbfile + padded[6:]

			out <- XMLRecord{Index: idx, Ident: id, Data: bytes}
		}
	}

	go func(archiveBase string, inp <-chan string, out chan<- XMLRecord) {

		defer close(out)

		// read dbfile name
		for dbfile := range inp {

			pos := strings.Index(dbfile, ".")
			if pos >= 0 {
				// remove any suffix
				dbfile = dbfile[:pos]
			}

			streamOneArchive(archiveBase, dbfile, out)
		}
	}(archiveBase, inp, out)

	return out
}

// StreamArchiveContents opens a set of local archive files and streams the compressed XML records.
func StreamArchiveContents(db string, inp <-chan string) <-chan XMLRecord {

	if db == "" {
		fmt.Fprintf(os.Stderr, "Database not specified for archive content streamer")
		return nil
	}

	if inp == nil {
		fmt.Fprintf(os.Stderr, "Channel of archive names not specified for archive content streamer")
		return nil
	}

	// obtain paths from environment variable(s) or configuration file
	pths := ResolveArchivePaths(db)
	if pths == nil {
		fmt.Fprintf(os.Stderr, "Unable to get local archive configuration paths")
		return nil
	}

	archiveBase, ok := pths.GetLocalPath("Archive")
	if archiveBase == "" {
		fmt.Fprintf(os.Stderr, "Unable to get local archive path")
		return nil
	}

	if !ok {
		fmt.Fprintf(os.Stderr, "Local archive is not mounted")
		return nil
	}

	return streamArchiveData(archiveBase, inp)
}

// FetchOneRecord returns the XML from a single identifier
func FetchOneRecord(db, id string) string {

	if db == "" || id == "" {
		return ""
	}

	rarq := ReadArchiveRecords(db, false, StringToChan(id))

	if rarq == nil {
		return ""
	}

	for str := range rarq {
		if str != "" {
			return str
		}
	}

	return ""
}

// DecompressArchiveContents gunzips the individual compressed byte arrays.
func DecompressArchiveContents(inp <-chan XMLRecord) <-chan XMLRecord {

	if inp == nil {
		fmt.Fprintf(os.Stderr, "No input supplied for archive content decompressor")
		return nil
	}

	out := make(chan XMLRecord, chanDepth)
	if out == nil {
		fmt.Fprintf(os.Stderr, "Unable to create unzip channel")
		return nil
	}

	unGzip := func(wg *sync.WaitGroup, inp <-chan XMLRecord, out chan<- XMLRecord) {

		defer wg.Done()

		for ext := range inp {

			idx := ext.Index
			id := ext.Ident
			bytes := ext.Data

			if bytes == nil || len(bytes) < 1 {
				out <- XMLRecord{Index: idx}
				continue
			}

			text := gunzipOneRecord(bytes[:])

			// include only the uncompressed version
			out <- XMLRecord{Index: idx, Ident: id, Text: text}
		}
	}

	var wg sync.WaitGroup

	// launch multiple unzip goroutines
	for range numServe {
		wg.Add(1)
		go unGzip(&wg, inp, out)
	}

	// launch separate anonymous goroutine to wait until all unzippers are done
	go func() {
		wg.Wait()
		close(out)
	}()

	return out
}
