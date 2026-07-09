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
// File Name:  index.go
//
// Author:  Jonathan Kans
//
// ==========================================================================

package eutils

import (
	"bufio"
	"cmp"
	"compress/gzip"
	"container/heap"
	"fmt"
	"html"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
)

// INDEXED AND INVERTED FILE FORMATS

// Local archive indexing reads original records (such as PubmedArticle XML) and produces IdxDocument records.
// For PubMed and PMC databases, terms in Title and Title/Abstract fields include term positions as XML attributes:

/*

  ...
  <IdxDocument>
    <IdxUid>2539356</IdxUid>
    <IdxSearchFields>
      <UID>0002539356</UID>
      <SIZE>13751</SIZE>
      <YEAR>1989</YEAR>
      <DATE>1989 04</DATE>
      <RDAT>2019 05 08</RDAT>
      <JOUR>J Bacteriol</JOUR>
      <JOUR>2985120R</JOUR>
      <JOUR>0021-9193</JOUR>
      <JOUR>Journal of Bacteriology</JOUR>
      <JOUR>J Bacteriol</JOUR>
      <JOUR>0021-9193</JOUR>
      <VOL>171</VOL>
      <ISS>4</ISS>
      <PAGE>1904</PAGE>
      <LANG>eng</LANG>
      <ANUM>2</ANUM>
      <FAUT>Kans JA</FAUT>
      <LAUT>Casadaban MJ</LAUT>
      <AUTH>Kans JA</AUTH>
      <AUTH>Casadaban MJ</AUTH>
      <TITL pos="7">immunity</TITL>
      <TITL pos="1">nucleotide</TITL>
      <TITL pos="3">required</TITL>
      <TITL pos="2">sequences</TITL>
      <TITL pos="5">tn3</TITL>
      <TITL pos="6">transposition</TITL>
      <TIAB pos="145">38</TIAB>
      <TIAB pos="126">acting</TIAB>
      <TIAB pos="188">additional</TIAB>
      <TIAB pos="146">base</TIAB>
      <TIAB pos="125">cis</TIAB>
      <TIAB pos="172,178,187">conferred</TIAB>
      ...
      <PAIR>nucleotide sequences</PAIR>
      <PAIR>sequences required</PAIR>
      <PAIR>tn3 transposition</PAIR>
      <PAIR>transposition immunity</PAIR>
      <PTYP>Journal Article</PTYP>
      <PTYP>Research Support, U.S. Gov&#39;t, P.H.S.</PTYP>
      <DOI>9891 4191 4091 4 171 bj 8211 01</DOI>
      <PMCID>209839</PMCID>
      <PROP>Published In Print</PROP>
      <PROP>Has Abstract</PROP>
      <CODE>d001483</CODE>
      <CODE>d002874</CODE>
      ...
      <MESH>Plasmids</MESH>
      <MESH>Recombination, Genetic</MESH>
      <SUBS>DNA Transposable Elements</SUBS>
      <SUBS>DNA, Bacterial</SUBS>
    </IdxSearchFields>
  </IdxDocument>
  ...

*/

// Inversion reads a set of indexed documents and generates InvDocument records:

/*

  ...
  <InvDocument>
    <InvKey>transposition</InvKey>
    <InvFld>TIAB</InvFld>
    <InvTag>tra</InvTag>
    <TIAB pos="6,122">2539356</TIAB>
  </InvDocument>
  <InvDocument>
    <InvKey>transposition</InvKey>
    <InvFld>TITL</InvFld>
    <InvTag>tra</InvTag>
    <TITL pos="6">2539356</TITL>
  </InvDocument>
  <InvDocument>
    <InvKey>transposition immunity</InvKey>
    <InvFld>PAIR</InvFld>
    <InvTag>tra</InvTag>
    <PAIR>2539356</PAIR>
  </InvDocument>
  ...

*/

// In a local archive, separate ranges of record unique identifiers (UIDs) are indexed and
// inverted as groups, which allows incremental updating. For efficient merging of these
// subsets, and in order to produce term lists and postings files, inverted records should
// be sorted first by term prefix, or tag.

// Tag lengths may be increased from the default 3 characters by a look-up table of two-letter
// prefixes. This is done to simultaneously avoid large numbers of small files (an unnecessary
// burden on the file system) and huge single files (a burden on query resolution).

// However, to accommodate huge databases (such as almost a half billion RefSeq proteins),
// specific fields such as peptide pentamers (PENT), accession (ACCN) and identifier (UID),
// are forced to prefix length 4.

// The two methods for computing prefix length can cause conflicts in the initial inverted set,
// which is sorted by full term (InvKey). A subsequent resorting first by tag (InvTag) and then,
// (for identical tags) by InvKey, and finally by field (InvFld, which also makes a separate
// InvDocument), can resolve out-of-order initial records, such as:

// <InvDocument>
//   <InvKey>glyaa</InvKey>
//   <InvFld>PENT</InvFld>
//   <InvTag>glya</InvTag>
//   <PENT>354004494</PENT>

// coming before:

// <InvDocument>
//   <InvKey>glycoside hydrolase family 1 protein</InvKey>
//   <InvFld>PROD</InvFld>
//   <InvTag>gly</InvTag>
//   <PROD>354002706</PROD>

// Separate inverted index files are merged and used to produce term lists and postings file.
// These can then be searched by passing arguments to EDirect's "xsearch" script.

// ENTREZ2INDEX COMMAND GENERATOR

// MakeE2Commands reads command lines that have been run through xargs for use by Entrez indexing.
func MakeE2Commands(tform, idxargs string) []string {

	var acc []string

	// idxargs file contains one command or argument per line
	if idxargs == "" {
		return acc
	}

	inFile, err := os.Open(idxargs)
	if err != nil {
		DisplayError("Unable to open index argument array file: %s\n", err.Error())
		return acc
	}
	defer inFile.Close()

	scanr := bufio.NewScanner(inFile)
	if scanr == nil {
		DisplayError("Unable to create NewScanner")
		return acc
	}

	for scanr.Scan() {

		line := scanr.Text()

		// do NOT skip empty line or trim spaces, in order to allow "" or " " arguments

		acc = append(acc, line)
	}

	return acc
}

// UPDATE CACHED INVERTED-INDEX FILES FROM LOCAL ARCHIVE FOLDERS

// e2IndexConsumer callbacks have access to application-specific data as closures
type e2IndexConsumer func(inp <-chan XMLRecord) <-chan XMLRecord

// IndexAndInvertArchive explores archive files and incrementally updates inverted index files.
func IndexAndInvertArchive(db, recname string, csmr e2IndexConsumer) <-chan string {

	if db == "" {
		return nil
	}

	if csmr == nil {
		return nil
	}

	// obtain paths from environment variable(s)
	pths := ResolveArchivePaths(db)
	if pths == nil {
		DisplayError("Unable to get local archive configuration paths")
		os.Exit(1)
	}

	archiveBase, ok := pths.GetLocalPath("Archive")

	if archiveBase == "" {
		DisplayError("Unable to get local archive path")
		os.Exit(1)
	}
	if !ok {
		DisplayError("Local archive is not mounted")
		os.Exit(1)
	}

	invertBase, ok := pths.GetLocalPath("Invert")

	if invertBase == "" {
		DisplayError("Unable to get local invert path")
		os.Exit(1)
	}
	if !ok {
		DisplayError("Local invert directory is not mounted")
		os.Exit(1)
	}

	temporaryBase, ok := pths.GetLocalPath("Temporary")

	if temporaryBase == "" {
		DisplayError("Unable to get local temporary path")
		os.Exit(1)
	}
	if !ok {
		DisplayError("Local temporary directory is not mounted")
		os.Exit(1)
	}

	collectBase, ok := pths.GetLocalPath("Collect")

	if collectBase == "" {
		DisplayError("Unable to get local collect path")
		os.Exit(1)
	}
	if !ok {
		DisplayError("Local collect directory is not mounted")
		os.Exit(1)
	}

	// exploreLeafFolders recursively visits the local archive directory hierarchy,
	// and sends relative paths to leaf directories down a channel.
	exploreLeafFolders := func(base, path string) <-chan string {

		if base == "" {
			return nil
		}

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create archive explorer channel")
			os.Exit(1)
		}

		isTwoDigits := func(str string) bool {

			if len(str) != 2 {
				return false
			}

			ch := str[0]
			if ch < '0' || ch > '9' {
				return false
			}

			ch = str[1]
			if ch < '0' || ch > '9' {
				return false
			}

			return true
		}

		getSubFolderNames := func(base, path string) []string {

			dir := filepath.Join(base, path)

			contents, err := os.ReadDir(dir)
			if err != nil {
				return nil
			}

			dirs := make([]string, 0, 100)
			if dirs == nil {
				return nil
			}

			for _, item := range contents {
				if !item.IsDir() {
					continue
				}
				name := item.Name()
				if name == "" || !isTwoDigits(name) {
					continue
				}
				dirs = append(dirs, name)
			}

			if len(dirs) > 1 {
				// ensure folder names are sorted from 00 to 99
				slices.SortFunc(dirs, CompareNumericStringKeys)
			}

			return dirs
		}

		// recursive definition
		var visitSubFolders func(base, path, name string, out chan<- string)

		// visitSubFolders recurses to leaf directories
		visitSubFolders = func(base, path, name string, out chan<- string) {

			// find subdirectories of current folder
			dirs := getSubFolderNames(base, path)

			if dirs == nil || len(dirs) < 1 {

				// if no further subdirectories, report path to data files
				out <- path

				return
			}

			// otherwise continue descending another level
			for _, dr := range dirs {
				// skip Sentinels folder (on top level)
				if len(dr) != 2 || !IsAllDigits(dr) {
					continue
				}

				sub := filepath.Join(path, dr)
				nm := name + dr
				// recursively explore subdirectories
				visitSubFolders(base, sub, nm, out)
			}
		}

		go func(base, path string, out chan<- string) {

			defer close(out)

			visitSubFolders(base, path, "", out)
		}(base, path, out)

		return out
	}

	// examineLeafFiles returns a map of relative paths to allow quick detection
	// of missing files for incremental updating.
	examineLeafFiles := func(base, path, suffix string) map[string]bool {

		if base == "" || suffix == "" {
			return nil
		}

		dir := filepath.Join(base, path)

		contents, err := os.ReadDir(dir)
		if err != nil {
			return nil
		}

		fils := make(map[string]bool)

		for _, item := range contents {
			if item.IsDir() {
				continue
			}
			name := item.Name()
			if name == "" {
				continue
			}

			// for quick testing, uncomment the next command to only index 1/10 of archived files
			// if strings.HasSuffix(name, ".archive") && len(name) == 14 && name[5] != '0' { continue }

			if strings.HasSuffix(name, suffix) {
				name = strings.TrimSuffix(name, suffix)
				fils[name] = true
			}
		}

		return fils
	}

	findLeafFolders := func(archiveBase, invertBase, leaf string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create key explorer channel")
			os.Exit(1)
		}

		go func(archiveBase, invertBase, leaf string, out chan<- string) {

			defer close(out)

			rcvs := examineLeafFiles(archiveBase, leaf, ".archive")
			if rcvs == nil {
				return
			}

			keys := slices.SortedFunc(maps.Keys(rcvs), CompareNumericStringKeys)

			// look for equivalent files in invert directory
			idxs := examineLeafFiles(invertBase, leaf, ".inv.gz")

			for _, key := range keys {

				// skip existing index files
				if idxs != nil && len(idxs) > 0 && idxs[key] {
					continue
				}
				// missing files are either for new archive files or were deleted as stale by the last archive update

				// process one archive leaf folder at a time
				out <- key
			}
		}(archiveBase, invertBase, leaf, out)

		return out
	}

	saveToFile := func(base, path, file, suffix string, compress bool, inps <-chan string, inpt <-chan TextRecord) <-chan string {

		if inps == nil && inpt == nil {
			return nil
		}

		if inps != nil && inpt != nil {
			DisplayError("Multiple input arguments to saveToFile")
			return nil
		}

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create saveToFile channel")
			os.Exit(1)
		}

		go func(base, path, file, suffix string, inps <-chan string, inpt <-chan TextRecord, out chan<- string) {

			defer close(out)

			var (
				wrtr *bufio.Writer
				zpr  *gzip.Writer
				err  error
			)

			dpath := filepath.Join(base, path)
			if dpath == "" {
				return
			}

			err = os.MkdirAll(dpath, os.ModePerm)
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
				return
			}
			fpath := filepath.Join(dpath, file+suffix)
			if fpath == "" {
				return
			}

			// overwrites and truncates existing file
			fl, err := os.Create(fpath)
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
				return
			}

			if compress {
				zpr, err = gzip.NewWriterLevel(fl, gzip.BestSpeed)
				if err != nil {
					DisplayError("Unable to create compressor")
					os.Exit(1)
				}
				wrtr = bufio.NewWriter(zpr)
			} else {
				wrtr = bufio.NewWriter(fl)
			}

			// write contents
			last := ""

			if inps != nil {
				for str := range inps {
					if str == "" {
						continue
					}
					wrtr.WriteString(str)
					last = str
				}
			} else if inpt != nil {
				for curr := range inpt {
					str := curr.Text
					if str == "" {
						continue
					}
					wrtr.WriteString(str)
					last = str
				}
			}

			if !strings.HasSuffix(last, "\n") {
				wrtr.WriteString("\n")
			}

			err = wrtr.Flush()
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
				return
			}

			if compress {
				err = zpr.Close()
				if err != nil {
					fmt.Fprintf(os.Stderr, "%s\n", err.Error())
					return
				}
			}

			// fl.Sync()

			err = fl.Close()
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
				return
			}

			out <- file
		}(base, path, file, suffix, inps, inpt, out)

		return out
	}

	oneHundredSeconds := "                                                                                                    "

	printElapsedSeconds := func(sttTime time.Time, dots uint32, indent bool) {

		stpTime := time.Now()
		duration := stpTime.Sub(sttTime)
		seconds := float64(duration.Nanoseconds()) / 1e9

		// calculate leading spaces so seconds values line up even on lines
		padding := ""
		padLen := 100 - dots
		if padLen > 0 && padLen <= 100 {
			padding = oneHundredSeconds[:padLen]
		}

		if indent {
			fmt.Fprintf(os.Stderr, "%s %5.*f\n      ", padding, 1, seconds)
		} else {
			fmt.Fprintf(os.Stderr, "%s %5.*f\n", padding, 1, seconds)
		}
	}

	collectMissingFiles := func(archiveBase, temporaryBase, leaf string, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create collector channel")
			os.Exit(1)
		}

		sttTime := time.Now()

		var dots atomic.Uint32

		collector := func(wg *sync.WaitGroup, archiveBase, temporaryBase, leaf string, inp <-chan string, out chan<- string) {

			defer wg.Done()

			for key := range inp {

				dpath := filepath.Join(archiveBase, leaf)
				fpath := filepath.Join(dpath, key+".archive")

				fl, err := os.Open(fpath)
				if err != nil {
					DisplayError("Unable to open file '%s' for collection", fpath)
					continue
				}
				defer fl.Close()

				sacq := StreamArchiveComponents(fl)
				stfq := saveToFile(temporaryBase, "", key, ".xml", false, sacq, nil)

				for fl := range stfq {
					out <- fl
				}

				// print progress dots for one leaf folder on the same line
				fmt.Fprintf(os.Stderr, ".")
				dots.Add(1)
			}
		}

		var wg sync.WaitGroup

		for range numServe {
			wg.Add(1)
			go collector(&wg, archiveBase, temporaryBase, leaf, inp, out)
		}

		go func() {
			wg.Wait()
			close(out)
			printElapsedSeconds(sttTime, dots.Load(), true)
		}()

		return out
	}

	indexMissingFiles := func(temporaryBase, leaf string, csmr e2IndexConsumer, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create indexer channel")
			os.Exit(1)
		}

		sttTime := time.Now()

		var dots atomic.Uint32

		cleanIndexFilesEx := func(inp <-chan XMLRecord) <-chan string {

			if inp == nil {
				DisplayError("No input to index cleaner")
				os.Exit(1)
			}

			out := make(chan string, chanDepth)
			if out == nil {
				DisplayError("Unable to create index cleaner channel")
				os.Exit(1)
			}

			go func(inp <-chan XMLRecord, out chan<- string) {

				defer close(out)

				re := regexp.MustCompile(">[ \n\r\t]*<")

				for curr := range inp {

					str := curr.Text

					if str == "" {
						continue
					}

					// clean up white space between stop tag and next start tag, replacing with a single newline
					str = re.ReplaceAllString(str, ">\n<")

					out <- str[:]
				}
			}(inp, out)

			return out
		}

		indexer := func(wg *sync.WaitGroup, temporaryBase, leaf string, csmr e2IndexConsumer, inp <-chan string, out chan<- string) {

			defer wg.Done()

			for key := range inp {

				fpath := filepath.Join(temporaryBase, key+".xml")

				f, err := os.Open(fpath)
				if err != nil {
					DisplayError("Unable to open file '%s' for indexing", fpath)
					continue
				}
				defer f.Close()

				// use full XML parser to ensure recursive records are handled properly
				rdr := CreateXMLStreamer(f, nil)
				xmlq := CreateXMLProducer(recname, "", false, rdr)
				// callback passes cmds and transform values as closures to xtract createConsumers
				tblq := csmr(xmlq)
				// simple cleanup of XML formatting
				cifq := cleanIndexFilesEx(tblq)
				stfq := saveToFile(temporaryBase, "", key, ".e2x", false, cifq, nil)

				for fl := range stfq {
					out <- fl
				}

				// print progress dots for one leaf folder on the same line
				fmt.Fprintf(os.Stderr, ".")
				dots.Add(1)
			}
		}

		var wg sync.WaitGroup

		for range numServe {
			wg.Add(1)
			go indexer(&wg, temporaryBase, leaf, csmr, inp, out)
		}

		go func() {
			wg.Wait()
			close(out)
			printElapsedSeconds(sttTime, dots.Load(), true)
		}()

		return out
	}

	invertMissingFiles := func(temporaryBase, leaf string, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create key explorer channel")
			os.Exit(1)
		}

		sttTime := time.Now()

		var dots atomic.Uint32

		var count atomic.Uint32

		inverter := func(wg *sync.WaitGroup, temporaryBase, leaf string, inp <-chan string, out chan<- string) {

			defer wg.Done()

			for key := range inp {

				fpath := filepath.Join(temporaryBase, key+".e2x")

				f, err := os.Open(fpath)
				if err != nil {
					DisplayError("Unable to open file '%s' for inversion", fpath)
					continue
				}
				defer f.Close()

				// can use simpler text parser for non-recursive IdxDocument XML start tag
				rdr := CreateTextStreamer(f)
				txtq := CreateTextProducer("<IdxDocument>", "", "", 0, 0, rdr)
				iifq := InvertIndexedFile(nil, txtq)
				stfq := saveToFile(temporaryBase, "", key, ".inv", false, iifq, nil)

				for fl := range stfq {
					out <- fl
				}

				// print progress dots for one leaf folder on the same line
				fmt.Fprintf(os.Stderr, ".")
				dots.Add(1)

				// force periodic garbage collection to prevent memory pressure
				count.Add(1)
				if count.Load() > 10 {
					count.Store(0)
					runtime.GC()
					debug.FreeOSMemory()
				}
			}
		}

		var wg sync.WaitGroup

		// launch several inverter goroutines, titrated (by using numProcs instead of numServe)
		// to keep CPUs active but avoid causing system memory pressure
		for range numProcs {
			wg.Add(1)
			go inverter(&wg, temporaryBase, leaf, inp, out)
		}

		go func() {
			wg.Wait()
			close(out)
			printElapsedSeconds(sttTime, dots.Load(), true)
		}()

		return out
	}

	compressMissingFiles := func(temporaryBase, invertBase, leaf string, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create key compressor channel")
			os.Exit(1)
		}

		sttTime := time.Now()

		var dots atomic.Uint32

		compressor := func(wg *sync.WaitGroup, temporaryBase, invertBase, leaf string, inp <-chan string, out chan<- string) {

			defer wg.Done()

			for key := range inp {

				fpath := filepath.Join(temporaryBase, key+".inv")

				f, err := os.Open(fpath)
				if err != nil {
					DisplayError("Unable to open file '%s' for compression", fpath)
					continue
				}
				defer f.Close()

				// can use simpler text parser for non-recursive InvDocument XML start tag
				rdr := CreateTextStreamer(f)
				txtq := CreateTextProducer("<InvDocument>", "", "", 0, 0, rdr)
				stfq := saveToFile(invertBase, leaf, key, ".inv.gz", true, nil, txtq)

				for fl := range stfq {
					out <- fl
				}

				// print progress dots for one leaf folder on the same line
				fmt.Fprintf(os.Stderr, ".")
				dots.Add(1)
			}
		}

		var wg sync.WaitGroup

		for range numServe {
			wg.Add(1)
			go compressor(&wg, temporaryBase, invertBase, leaf, inp, out)
		}

		go func() {
			wg.Wait()
			close(out)
			printElapsedSeconds(sttTime, dots.Load(), false)
		}()

		return out
	}

	processLeafFolders := func(archiveBase, invertBase, temporaryBase string, csmr e2IndexConsumer, inp <-chan string) <-chan string {

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create leaf explorer channel")
			os.Exit(1)
		}

		go func(archiveBase, invertBase, temporaryBase string, csmr e2IndexConsumer, inp <-chan string, out chan<- string) {

			defer close(out)

			doSingleLeaf := func(archiveBase, invertBase, temporaryBase, leaf string, csmr e2IndexConsumer) {

				dlfq := findLeafFolders(archiveBase, invertBase, leaf)
				leaves := ChanToSlice(dlfq)

				if len(leaves) < 1 {
					return
				}
				slices.Sort(leaves)

				cmfq := collectMissingFiles(archiveBase, temporaryBase, leaf, SliceToChan(leaves))
				collected := ChanToSlice(cmfq)

				if len(collected) < 1 {
					fmt.Fprintf(os.Stderr, "Unable to collect %s ", leaf)
					return
				}
				slices.Sort(collected)

				imfq := indexMissingFiles(temporaryBase, leaf, csmr, SliceToChan(collected))
				indexed := ChanToSlice(imfq)

				if len(indexed) < 1 {
					fmt.Fprintf(os.Stderr, "Unable to index %s ", leaf)
					return
				}
				slices.Sort(indexed)

				vmfq := invertMissingFiles(temporaryBase, leaf, SliceToChan(indexed))
				inverted := ChanToSlice(vmfq)

				if len(inverted) < 1 {
					fmt.Fprintf(os.Stderr, "Unable to invert %s ", leaf)
					return
				}
				slices.Sort(inverted)

				cmpq := compressMissingFiles(temporaryBase, invertBase, leaf, SliceToChan(inverted))
				compressed := ChanToSlice(cmpq)

				if len(compressed) < 1 {
					fmt.Fprintf(os.Stderr, "Unable to compress %s ", leaf)
					return
				}
				slices.Sort(compressed)

				// uncomment return statement to leave intermediate files for testing
				// return

				for _, fl := range compressed {
					for _, suffix := range []string{".xml", ".e2x", ".inv"} {
						fpath := filepath.Join(temporaryBase, fl+suffix)
						err := os.Remove(fpath)
						if err != nil {
							fmt.Fprintf(os.Stderr, "Unable to remove %s: %s\n", fpath, err.Error())
						}
					}
				}

				runtime.GC()
				runtime.Gosched()
				debug.FreeOSMemory()
			}

			for leaf := range inp {

				// print current archive leaf directory (e.g., "00/02 ")
				fmt.Fprintf(os.Stderr, "%s ", leaf)

				doSingleLeaf(archiveBase, invertBase, temporaryBase, leaf, csmr)

				fmt.Fprintf(os.Stderr, "\n")

				out <- leaf

				// uncomment break statement to only process one folder for testing
				// break
			}
		}(archiveBase, invertBase, temporaryBase, csmr, inp, out)

		return out
	}

	elfq := exploreLeafFolders(archiveBase, "")
	iilq := processLeafFolders(archiveBase, invertBase, temporaryBase, csmr, elfq)

	if elfq == nil || iilq == nil {
		DisplayError("Unable to create exploreLeafFolders")
		os.Exit(1)
	}

	return iilq
}

// INDEX INVERSION FUNCTION

type Invert struct {
	Tag   string
	Term  string
	Field string
	UID   string
	Attrs string
}

// InvertHeap methods satisfy heap.Interface
type InvertHeap []Invert

func (h InvertHeap) Len() int {
	return len(h)
}

func (h InvertHeap) Less(i, j int) bool {

	// 2 to 4 letter term prefix is used for sequential merging of inverted index files
	// from an entire database prior to final creation of term lists and postings tables
	res := cmp.Compare(h[i].Tag, h[j].Tag)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	res = cmp.Compare(h[i].Term, h[j].Term)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	res = cmp.Compare(h[i].Field, h[j].Field)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	res = CompareNumericStringKeys(h[i].UID, h[j].UID)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	return i < j
}

func (h InvertHeap) Swap(i, j int) {
	h[i], h[j] = h[j], h[i]
}

// Push works on pointer to InvertHeap
func (h *InvertHeap) Push(x interface{}) {
	*h = append(*h, x.(Invert))
}

// Pop works on pointer to InvertHeap
func (h *InvertHeap) Pop() interface{} {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[0 : n-1]
	return x
}

// InvertIndexedFile reads IdxDocument XML records and writes InvDocument XML records
func InvertIndexedFile(inps <-chan string, inpt <-chan TextRecord) <-chan string {

	if inps == nil && inpt == nil {
		return nil
	}

	if inps != nil && inpt != nil {
		DisplayError("Multiple input arguments to InvertIndexedFile")
		return nil
	}

	indexDispenser := func(inps <-chan string, inpt <-chan TextRecord) <-chan Invert {

		if inps == nil && inpt == nil {
			return nil
		}

		out := make(chan Invert, chanDepth)
		if out == nil {
			DisplayError("Unable to create dispenser channel")
			os.Exit(1)
		}

		go func(inps <-chan string, inpt <-chan TextRecord, out chan<- Invert) {

			defer close(out)

			currUID := ""

			doDispense := func(fld, pos, term string) {

				if fld == "IdxUid" {
					currUID = term
					return
				}

				term = html.UnescapeString(term)

				// expand Greek letters, anglicize characters in other alphabets
				if IsNotASCII(term) {
					term = TransformAccents(term, true, true)
					if HasAdjacentSpacesOrNewline(term) {
						term = CompressRunsOfSpaces(term)
					}
					term = UnicodeToASCII(term)
					if HasFlankingSpace(term) {
						term = strings.TrimSpace(term)
					}
				}

				term = strings.ToLower(term)

				// remove punctuation from term
				term = strings.Map(func(c rune) rune {
					if !unicode.IsLetter(c) && !unicode.IsDigit(c) && c != ' ' && c != '-' && c != '_' {
						return -1
					}
					return c
				}, term)

				term = strings.Replace(term, "_", " ", -1)
				term = strings.Replace(term, "-", " ", -1)

				if HasAdjacentSpacesOrNewline(term) {
					term = CompressRunsOfSpaces(term)
				}
				if HasFlankingSpace(term) {
					term = strings.TrimSpace(term)
				}

				if term == "" || currUID == "" {
					return
				}

				tag := IdentifierKey(term, fld)
				// underscore is only for file name, revert to space for proper alphabetical sorting
				tag = strings.Replace(tag, "_", " ", -1)
				// do NOT call TrimSpace - internal or trailing spaces will be replaced by underscore
				// only when tag is used for file and directory names
				tag = strings.TrimLeft(tag, " ")

				out <- Invert{Tag: tag, Term: term, Field: fld, UID: currUID, Attrs: pos}
			}

			// read partitioned IdxDocument XML records
			if inps != nil {
				for str := range inps {
					StreamValues(str[:], "IdxDocument", doDispense)
				}
			} else if inpt != nil {
				for curr := range inpt {
					str := curr.Text
					StreamValues(str[:], "IdxDocument", doDispense)
				}
			}
		}(inps, inpt, out)

		return out
	}

	indexInverter := func(inp <-chan Invert) <-chan Invert {

		if inp == nil {
			return nil
		}

		out := make(chan Invert, chanDepth)
		if out == nil {
			DisplayError("Unable to create inverter channel")
			os.Exit(1)
		}

		go func(inp <-chan Invert, out chan<- Invert) {

			defer close(out)

			// initialize empty heap
			hp := &InvertHeap{}
			heap.Init(hp)

			// read all objects into heap
			for curr := range inp {
				heap.Push(hp, curr)
			}

			prevTag, prevTerm, prevField, prevUID := "", "", "", ""

			for hp.Len() > 0 {

				// sort by removing lowest item from heap
				curr := heap.Pop(hp).(Invert)

				// fmt.Fprintf(os.Stderr, "%s\t%s\t%s\t%s\t%s\t\n", curr.Tag, curr.Field, curr.UID, curr.Term, curr.Attrs)

				// remove duplicate entries
				if curr.Tag == prevTag && curr.Term == prevTerm && curr.Field == prevField && curr.UID == prevUID {
					continue
				}

				// write to output channel in sorted order
				out <- curr

				// remember last index line
				prevTag, prevTerm, prevField, prevUID = curr.Tag, curr.Term, curr.Field, curr.UID
			}
		}(inp, out)

		return out
	}

	/*
		indexMultiplexer := func(inp <-chan Invert) <-chan Invert {

			if inp == nil {
				return nil
			}

			out := make(chan Invert, chanDepth)
			if out == nil {
				DisplayError("Unable to create multiplexer channel")
				os.Exit(1)
			}

			prefixes := "0123456789abcdefghijklmnopqrstuvwxyz"

			sendToInverter := make(map[rune]chan Invert)
			getFromInverter := make(map[rune]<-chan Invert)

			for _, ch := range prefixes {
				sendToInverter[ch] = make(chan Invert, chanDepth)
				chn := indexInverter(sendToInverter[ch])
				getFromInverter[ch] = chn
			}

			go func(inp <-chan Invert, out chan<- Invert) {

				defer close(out)

				for curr := range inp {
					if curr.Term == "" {
						continue
					}
					ch := rune(curr.Term[0])
					// dispatch to appropriate inverter
					chn, ok := sendToInverter[ch]
					if !ok {
						continue
					}
					chn <- curr
				}

				for _, ch := range prefixes {
					close(sendToInverter[ch])
				}

				for _, ch := range prefixes {
					chn, ok := getFromInverter[ch]
					if !ok {
						continue
					}
					for curr := range chn {
						out <- curr
					}
				}
			}(inp, out)

			return out
		}
	*/

	indexResolver := func(inp <-chan Invert) <-chan string {

		if inp == nil {
			return nil
		}

		out := make(chan string, chanDepth)
		if out == nil {
			DisplayError("Unable to create resolver channel")
			os.Exit(1)
		}

		go func(inp <-chan Invert, out chan<- string) {

			defer close(out)

			var buffer strings.Builder

			prevTag, prevTerm, prevField, prevUID := "", "", "", ""

			finishRecord := func() {
				// finish old record
				buffer.WriteString("</InvDocument>\n")
				str := buffer.String()
				buffer.Reset()
				// send to output
				out <- str
			}

			for curr := range inp {

				// fmt.Fprintf(os.Stderr, "%s\t%s\t%s\t%s\t%s\t\n", curr.Tag, curr.Field, curr.UID, curr.Term, curr.Attrs)

				if prevTag != "" && buffer.Len() > 0 {
					if curr.Tag != prevTag || curr.Term != prevTerm || curr.Field != prevField || curr.UID != prevUID {
						finishRecord()
					}
				}

				if buffer.Len() == 0 {
					// if start of new record, write header
					buffer.WriteString("<InvDocument>\n  <InvKey>")
					buffer.WriteString(curr.Term)
					buffer.WriteString("</InvKey>\n  <InvFld>")
					buffer.WriteString(curr.Field)
					buffer.WriteString("</InvFld>\n  <InvTag>")
					buffer.WriteString(curr.Tag)
					buffer.WriteString("</InvTag>\n")
				}

				// write one index item
				buffer.WriteString("  <")
				buffer.WriteString(curr.Field)
				if curr.Attrs != "" {
					buffer.WriteString(" ")
					buffer.WriteString(curr.Attrs)
				}
				buffer.WriteString(">")
				buffer.WriteString(curr.UID)
				buffer.WriteString("</")
				buffer.WriteString(curr.Field)
				buffer.WriteString(">\n")

				prevTag, prevTerm, prevField, prevUID = curr.Tag, curr.Term, curr.Field, curr.UID
			}

			if prevTag != "" && buffer.Len() > 0 {
				finishRecord()
			}
		}(inp, out)

		return out
	}

	idsq := indexDispenser(inps, inpt)
	invq := indexInverter(idsq)
	idrq := indexResolver(invq)

	if idsq == nil || invq == nil || idrq == nil {
		return nil
	}

	return idrq
}
