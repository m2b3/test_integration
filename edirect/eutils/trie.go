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
// File Name:  trie.go
//
// Author:  Jonathan Kans
//
// ==========================================================================

package eutils

import (
	"path/filepath"
	"strings"
	"unicode"
)

// LinkLen controls the number of directory levels for link terms
const LinkLen = 6

// PostLen directory depth parameters are based on the observed size distribution of PubMed and Peptide indices
var PostLen = map[string]int{
	"00": 3,
	"co": 3,
	"d0": 3,
	"eo": 3,
	"in": 3,
	"pr": 3,
	"re": 3,
}

const PADLENGTH = 10

// PadNumericID returns 10-character leading zero-padded numeric identifier
func PadNumericID(id string) string {

	// "2539356"

	if len(id) > 64 {
		return ""
	}

	if !IsAllDigits(id) {
		DisplayError("PadNumericID argument '%s' is not numeric", id)
		return ""
	}

	// pad numeric identifier to 10 characters with leading zeros
	ln := len(id)
	if ln < PADLENGTH {
		zeros := "0000000000"
		id = zeros[ln:] + id
	}

	// "0002539356"

	return id
}

// ArchiveTrie generates the path and file name for indexed local archive files
func ArchiveTrie(id string) (string, string) {

	// "2539356"

	if len(id) > 64 {
		return "", ""
	}

	str := PadNumericID(id)

	// "0002539356"

	var arry [132]rune

	i := 0

	between := 0
	doSlash := false

	// divide UID into character pairs
	for _, ch := range str {
		if doSlash {
			arry[i] = '/'
			i++
			doSlash = false
		}
		arry[i] = ch
		i++
		between++
		if between > 1 {
			doSlash = true
			between = 0
		}
	}

	// trim back three subdirectory levels
	i -= 9

	res := string(arry[:i])

	if !strings.HasSuffix(res, "/") {
		arry[i] = '/'
		i++
		res = string(arry[:i])
	}

	// return the 6-digit .xml file name for holding and indexing 10,000 records
	idx := PadNumericID(id)

	// limit trie to first 6 characters
	if len(idx) > 6 {
		idx = idx[:6]
	}

	// "00/02/", "000253"

	return res, idx
}

// InvertTrie generates the path and file name for inverted index files
func InvertTrie(id string) (string, string) {

	if len(id) > 64 {
		return "", ""
	}

	// "2539356"

	dir, inv := ArchiveTrie(id)

	// "00/02/", "00025393"

	// limit invert directory to first 5 characters
	if len(dir) > 5 {
		dir = dir[:5]
	}
	// limit invert trie to first 6 characters
	if len(inv) > 6 {
		inv = inv[:6]
	}

	// "00/02", "000253"

	return dir, inv
}

// PostingDir returns directory trie (without slashes) for location of indices for a given term
func PostingDir(term, field string) string {

	if len(term) < 3 {
		return term
	}

	if field == "ACCN" || strings.HasPrefix(field, "ACCN-") {
		if len(term) > 3 {
			return term[:4]
		}
	}

	if field == "UID" || strings.HasPrefix(field, "UID-") {
		if len(term) > 3 {
			return term[:4]
		}
	}

	if field == "PENT" {
		if len(term) > 2 {
			return term[:3]
		}
	}

	key := term[:2]

	num, ok := PostLen[key]
	if ok && len(term) >= num {
		return term[:num]
	}

	return term[:2]
}

// IdentifierKey cleans up a term then returns the posting directory
func IdentifierKey(term, field string) string {

	// remove punctuation from term
	key := strings.Map(func(c rune) rune {
		if !unicode.IsLetter(c) && !unicode.IsDigit(c) && c != ' ' && c != '-' && c != '_' {
			return -1
		}
		return c
	}, term)

	key = strings.Replace(key, " ", "_", -1)
	key = strings.Replace(key, "-", "_", -1)

	// use first 2, 3, or 4 characters of identifier for directory
	key = PostingDir(key, field)

	return key
}

// PostingsTrie splits a string into characters, separated by path delimiting slashes
func PostingsTrie(term, file string) (string, string) {

	// "cancer"

	if len(term) > 256 {
		return "", term
	}

	// use first few characters of identifier for directory
	key := IdentifierKey(term, file)

	str := key

	var arry [516]rune

	if IsNotASCII(str) {
		// expand Greek letters, anglicize characters in other alphabets
		str = TransformAccents(str, true, true)
	}
	if HasAdjacentSpaces(str) {
		str = CompressRunsOfSpaces(str)
	}
	str = strings.TrimSpace(str)

	i := 0
	doSlash := false

	for _, ch := range str {
		if doSlash {
			arry[i] = '/'
			i++
		}
		if ch == ' ' {
			ch = '_'
		}
		if !unicode.IsLetter(ch) && !unicode.IsDigit(ch) {
			ch = '_'
		}
		arry[i] = ch
		i++
		doSlash = true
	}

	// "c/a/n/c", "canc"

	return strings.ToLower(string(arry[:i])), key
}

// PostingPath constructs a Postings directory subpath for a given term prefix
func PostingPath(prom, field, term string, isLink bool) (string, string) {

	// "/Volumes/archive/pubmed/Postings", "TIAB", "cancer"

	if isLink {
		dir, _ := LinksTrie(term, false)
		if dir == "" {
			return "", ""
		}
		dpath := filepath.Join(prom, field, dir)

		return dpath, term
	}

	// use first few characters of identifier for directory
	key := IdentifierKey(term, field)

	dir, _ := PostingsTrie(term, field)
	if dir == "" {
		return "", ""
	}

	dpath := filepath.Join(prom, field, dir)

	// "/Volumes/archive/pubmed/Postings/TIAB/c/a/n/c", "canc"

	return dpath, key
}

// LinksTrie generates the path and file name for link inverted index files
func LinksTrie(id string, pad bool) (string, string) {

	// "2539356"

	if len(id) > 64 {
		return "", id
	}

	str := id

	if pad {

		// true from ProcessLinks, false from PostingPath (indexed link terms are already zero-padded)

		str = PadNumericID(id)

		// "0002539356"
	}

	// links always use 4 directory levels of padded identifiers, grouped numerically,
	// so ProcessLinks may be able to get multiple nearby links with fewer reads
	if len(str) > LinkLen {
		str = str[:LinkLen]
	}

	// "0253"

	var arry [132]rune

	i := 0
	doSlash := false

	for _, ch := range str {
		if doSlash {
			arry[i] = '/'
			i++
		}
		if ch == ' ' {
			ch = '_'
		}
		if !unicode.IsLetter(ch) && !unicode.IsDigit(ch) {
			ch = '_'
		}
		arry[i] = ch
		i++
		doSlash = true
	}

	res := string(arry[:i])

	if !strings.HasSuffix(res, "/") {
		arry[i] = '/'
		i++
		res = string(arry[:i])
	}

	// "0/2/5/3/", "0253" (pad true)

	return res, str
}
