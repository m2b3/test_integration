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
// File Name:  merge.go
//
// Author:  Jonathan Kans
//
// ==========================================================================

package eutils

import (
	"bufio"
	"cmp"
	"container/heap"
	"fmt"
	"github.com/klauspost/pgzip"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

// parseInvDocument returns the <InvKey>, <InvFld>, and <InvTag> values from an InvDocument record.
func parseInvDocument(txt string) (string, string, string, string) {

	if txt == "" {
		return "", "", "", ""
	}

	key, fld, tag, uid := "", "", "", ""

	txt = strings.TrimSpace(txt)
	txt = strings.TrimPrefix(txt, "<InvDocument>\n")
	txt = strings.TrimSuffix(txt, "</InvDocument>")

	pos := strings.IndexByte(txt, '\n')
	if pos < 0 {
		return "", "", "", ""
	}
	key, txt = txt[:pos], txt[pos+1:]

	pos = strings.IndexByte(txt, '\n')
	if pos < 0 {
		return "", "", "", ""
	}
	fld, txt = txt[:pos], txt[pos+1:]

	pos = strings.IndexByte(txt, '\n')
	if pos < 0 {
		return "", "", "", ""
	}
	tag, txt = txt[:pos], txt[pos+1:]

	pos = strings.IndexByte(txt, '\n')
	if pos < 0 {
		return "", "", "", ""
	}
	uid, txt = txt[:pos], txt[pos+1:]

	key = strings.TrimSpace(key)
	key = strings.TrimPrefix(key, "<InvKey>")
	key = strings.TrimSuffix(key, "</InvKey>")

	fld = strings.TrimSpace(fld)
	fld = strings.TrimPrefix(fld, "<InvFld>")
	fld = strings.TrimSuffix(fld, "</InvFld>")

	// do NOT call TrimSpace - internal or trailing spaces will be substituted by underscore in file and directory names
	tag = strings.TrimLeft(tag, " ")
	tag = strings.TrimPrefix(tag, "<InvTag>")
	tag = strings.TrimSuffix(tag, "</InvTag>")

	uid = strings.TrimSpace(key)
	uid = strings.TrimPrefix(key, "<UID>")
	uid = strings.TrimSuffix(key, "</UID>")

	return key, fld, tag, uid
}

// Plex allows distribution of indexing
type Plex struct {
	Ident string
	Field string
	Tag   string
	Text  string
	UID   string
	FNum  int
	Index int
}

// PlexHeap methods satisfy heap.Interface
type PlexHeap []Plex

func (h PlexHeap) Len() int {
	return len(h)
}
func (h PlexHeap) Less(i, j int) bool {

	res := cmp.Compare(h[i].Tag, h[j].Tag)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	res = cmp.Compare(h[i].Ident, h[j].Ident)
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

	res = cmp.Compare(h[i].FNum, h[j].FNum)
	if res < 0 {
		return true
	}
	if res > 0 {
		return false
	}

	return i < j
}
func (h PlexHeap) Swap(i, j int) {
	h[i], h[j] = h[j], h[i]
}

// Push works on pointer to PlexHeap
func (h *PlexHeap) Push(x interface{}) {
	*h = append(*h, x.(Plex))
}

// Pop works on pointer to PlexHeap
func (h *PlexHeap) Pop() interface{} {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[0 : n-1]
	return x
}

// CreatePresenters creates one channel per input file
func CreatePresenters(files []string) []<-chan Plex {

	if files == nil {
		return nil
	}

	numFiles := len(files)
	if numFiles < 1 {
		DisplayError("Not enough inverted files to merge")
		os.Exit(1)
	}

	chns := make([]<-chan Plex, numFiles)
	if chns == nil {
		DisplayError("Unable to create presenter channel array")
		os.Exit(1)
	}

	// xmlPresenter sends partitioned XML strings through channel
	xmlPresenter := func(fileNum int, fileName string, out chan<- Plex) {

		// close this channel instance when all records have been processed
		defer close(out)

		f, err := os.Open(fileName)
		if err != nil {
			DisplayError("Unable to open input file '%s': %s", fileName, err.Error())
			os.Exit(1)
		}

		// close input file when all records have been processed
		defer f.Close()

		var in io.Reader

		in = f

		// if suffix is ".gz", use decompressor
		iszip := false
		if strings.HasSuffix(fileName, ".gz") {
			iszip = true
		}

		if iszip {
			brd := bufio.NewReader(f)
			if brd == nil {
				DisplayError("Unable to create buffered reader on '%s'", fileName)
				os.Exit(1)
			}
			// using parallel pgzip for better performance on large files
			zpr, err := pgzip.NewReader(brd)
			if err != nil {
				DisplayError("Unable to create decompressor on '%s': %s", fileName, err.Error())
				os.Exit(1)
			}

			// close decompressor when all records have been processed
			defer zpr.Close()

			// use decompressor for reading file
			in = zpr
		}

		rdr := CreateXMLStreamer(in, nil)

		if rdr == nil {
			DisplayError("Unable to create XML Block Reader")
			os.Exit(1)
		}

		// partition all input by pattern and send XML substring through channel
		PartitionXML("InvDocument", "", false, rdr,
			func(str string) {
				id, fld, tag, uid := parseInvDocument(str[:])

				out <- Plex{Ident: id, Field: fld, Tag: tag, Text: str, UID: uid, FNum: fileNum, Index: 0}
			})
	}

	if len(files) > 1 {
		slices.Sort(files)
	}

	// launch multiple presenter goroutines
	for fnum, str := range files {

		chn := make(chan Plex, chanDepth)
		if chn == nil {
			DisplayError("Unable to create presenter channel")
			os.Exit(1)
		}

		go xmlPresenter(fnum, str, chn)

		// save this channel instance into array of channels
		chns[fnum] = chn
	}

	// no need for separate anonymous goroutine to wait until all presenters are done

	return chns
}

// CreateManifold reads from each file, sends merged postings in sorted order
func CreateManifold(inp []<-chan Plex) <-chan Plex {

	if inp == nil {
		return nil
	}

	out := make(chan Plex, chanDepth)
	if out == nil {
		DisplayError("Unable to create manifold channel")
		os.Exit(1)
	}

	headTag := "</InvTag>\n"
	headLen := len(headTag)
	tailTag := "</InvDocument>"
	tailTagn := "</InvDocument>\n"

	// restores alphabetical order of merged postings
	go func(inp []<-chan Plex, out chan<- Plex) {

		// close channel when all records have been processed
		defer close(out)

		// initialize empty heap
		hp := &PlexHeap{}
		heap.Init(hp)

		// read first object from all input channels in turn
		for _, chn := range inp {
			plx, ok := <-chn
			if ok {
				heap.Push(hp, plx)
			}
		}

		// collect strings with same identifier
		var buffer strings.Builder

		prevIdent := ""
		prevField := ""
		prevTag := ""
		prevUID := ""
		prevFNum := 0
		rec := 0

		writeMergedPosting := func() {

			if buffer.Len() > 0 {

				// close InvDocument XML
				buffer.WriteString(tailTagn)

				txt := buffer.String()

				if txt != "" {
					rec++
					// send set from previous identifier to output channel
					out <- Plex{Ident: prevIdent, Field: prevField, Tag: prevTag, Text: txt, UID: prevUID, FNum: prevFNum, Index: rec}
				}

				// reset the buffer
				buffer.Reset()
			}
		}

		// reading from heap returns objects in alphabetical order
		for hp.Len() > 0 {

			// remove lowest item from heap, use interface type assertion
			curr := heap.Pop(hp).(Plex)

			// compare adjacent record identifiers
			if prevIdent == curr.Ident && prevField == curr.Field && prevTag == curr.Tag {

				txt := curr.Text[:]
				if strings.HasSuffix(txt, tailTag) {
					txt = strings.TrimSuffix(txt, tailTag)
				}

				pos := strings.Index(txt, headTag)
				if pos >= 0 {
					txt = txt[pos+headLen:]
				}

				// save next inverted object string
				buffer.WriteString(txt)
				if !strings.HasSuffix(txt, "\n") {
					buffer.WriteString("\n")
				}

			} else {

				if prevIdent != "" {
					writeMergedPosting()
				}

				// remember new identifier
				prevIdent = curr.Ident
				prevField = curr.Field
				prevTag = curr.Tag
				prevUID = curr.UID
				prevFNum = curr.FNum

				txt := curr.Text[:]
				if strings.HasSuffix(txt, tailTag) {
					txt = strings.TrimSuffix(txt, tailTag)
				}

				// save first inverted object with this identifier
				buffer.WriteString(txt)
				if !strings.HasSuffix(txt, "\n") {
					buffer.WriteString("\n")
				}
			}

			// read next object from channel that just supplied lowest item
			chn := inp[curr.FNum]
			plx, ok := <-chn
			if ok {
				heap.Push(hp, plx)
			}
		}

		writeMergedPosting()

	}(inp, out)

	return out
}

// CreateSplitter distributes adjacent records with the same identifier prefix
func CreateSplitter(mergePath, db string, zipp, isLink bool, inp <-chan Plex) <-chan string {

	if inp == nil {
		return nil
	}

	// obtain paths from environment variable(s)
	pths := ResolveArchivePaths(db)
	if pths == nil {
		DisplayError("Unable to get local archive configuration paths")
		os.Exit(1)
	}

	mergeBase, ok := pths.GetLocalPath("Merged")

	if mergeBase == "" {
		DisplayError("Unable to get local merged path")
		os.Exit(1)
	}
	if !ok {
		DisplayError("Local merged directory '%s' is not mounted", mergeBase)
		os.Exit(1)
	}

	out := make(chan string, chanDepth)
	if out == nil {
		DisplayError("Unable to create splitter channel")
		os.Exit(1)
	}

	openSaver := func(mergeBase, tag string, fnum int, zipp bool) (*os.File, *bufio.Writer, *pgzip.Writer) {

		var (
			fl   *os.File
			wrtr *bufio.Writer
			zpr  *pgzip.Writer
			err  error
		)

		sfx := ".mrg"
		if zipp {
			sfx += ".gz"
		}

		// restore underscore as substitute for space in file name
		tag = strings.Replace(tag, " ", "_", -1)

		fpath := filepath.Join(mergeBase, tag+sfx)
		if fpath == "" {
			return nil, nil, nil
		}

		// check to see if file is already present
		fileExists := false
		_, err = os.Stat(fpath)
		if err == nil {
			fileExists = true
		}

		if fileExists {
			// file should not exist once the new code is finalized, but if it does, open for appending
			fl, err = os.OpenFile(fpath, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0644)
			DisplayError("Appending to merge file '%s' from collected file %d\n", fpath, fnum)
		} else {
			// create new file - this would overwrite and truncate existing file
			fl, err = os.Create(fpath)
			// fmt.Fprintf(os.Stderr, "  %d  Create '%s'\n", fnum, fpath)
		}

		if err != nil {
			fmt.Fprintf(os.Stderr, "%s\n", err.Error())
			return nil, nil, nil
		}

		var out io.Writer

		out = fl

		if zipp {

			// using parallel pgzip for better performance on large files
			zpr, err = pgzip.NewWriterLevel(fl, pgzip.BestSpeed)
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
				return nil, nil, nil
			}

			out = zpr
		}

		// create buffered writer layer
		wrtr = bufio.NewWriter(out)
		if wrtr == nil {
			DisplayError("Unable to create bufio.NewWriter")
			return nil, nil, nil
		}

		return fl, wrtr, zpr
	}

	closeSaver := func(fl *os.File, wrtr *bufio.Writer, zpr *pgzip.Writer) {

		wrtr.Flush()
		if zpr != nil {
			zpr.Close()
		}
		// fl.Sync()

		err := fl.Close()
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s\n", err.Error())
		}
	}

	// distributes adjacent records with the same identifier prefix
	go func(inp <-chan Plex, out chan<- string) {

		// close channel when all records have been processed
		defer close(out)

		var (
			fl   *os.File
			wrtr *bufio.Writer
			zpr  *pgzip.Writer
		)

		currTag := ""
		prevTag := ""

		getCurrTag := func(ident, field string) string {

			// links always use first 6 characters of zero-padded identifier
			if isLink {
				if len(ident) > LinkLen {
					ident = ident[:LinkLen]
				}
				return ident
			}

			// use first few characters of identifier
			tag := IdentifierKey(ident, field)
			if tag == "" {
				return ""
			}
			// underscore is only for use in file name, revert to space for proper tag sorting
			tag = strings.Replace(tag, "_", " ", -1)

			if len(tag) > 2 {
				tag = tag[:2]
			}

			return tag
		}

		for curr := range inp {

			// use first few characters of identifier
			currTag = getCurrTag(curr.Ident, curr.Field)
			if currTag == "" {
				continue
			}

			if fl == nil {
				// open initial file
				fl, wrtr, zpr = openSaver(mergeBase, currTag, curr.FNum, zipp)
				if wrtr == nil {
					continue
				}

				// send first opening tag and indent
				wrtr.WriteString("<InvDocumentSet>\n  ")
			}

			// compare keys from adjacent term lists
			if prevTag != "" && prevTag != currTag {

				// WAS: after IdentifierKey converts space to underscore,
				// okay that x_ and x0 will be out of alphabetical order

				// send closing tag
				wrtr.WriteString("</InvDocumentSet>\n")

				closeSaver(fl, wrtr, zpr)

				out <- currTag

				// open next file
				fl, wrtr, zpr = openSaver(mergeBase, currTag, curr.FNum, zipp)
				if wrtr == nil {
					continue
				}

				// send opening tag and indent
				wrtr.WriteString("<InvDocumentSet>\n  ")
			}

			// send one InvDocument
			str := strings.TrimSpace(curr.Text)

			wrtr.WriteString(str)
			if !strings.HasSuffix(str, "\n") {
				wrtr.WriteString("\n")
			}

			prevTag = currTag
		}

		if prevTag != "" {

			// send last closing tag
			wrtr.WriteString("</InvDocumentSet>\n")

			closeSaver(fl, wrtr, zpr)

			out <- currTag
		}
	}(inp, out)

	return out
}
