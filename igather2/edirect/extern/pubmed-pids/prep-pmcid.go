// prep-pmcid.go

// Public domain notice for all NCBI EDirect scripts is located at:
// https://www.ncbi.nlm.nih.gov/books/NBK179288/#chapter6.Public_Domain_Notice

package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
	"unicode"
)

// copied from EDirect's eutils library

// ANSI escape codes for terminal color, highlight, and reverse
const (
	RED  = "\033[31m"
	BLUE = "\033[34m"
	BOLD = "\033[1m"
	RVRS = "\033[7m"
	INIT = "\033[0m"
	LOUD = INIT + RED + BOLD
	INVT = LOUD + RVRS
)

func displayError(format string, params ...any) {

	str := fmt.Sprintf(format, params...)
	fmt.Fprintf(os.Stderr, "\n%s ERROR: %s %s%s\n", INVT, LOUD, str, INIT)
}

func isAllDigits(str string) bool {

	for _, ch := range str {
		if !unicode.IsDigit(ch) {
			return false
		}
	}

	return true
}

const PADLENGTH = 10

func padNumericID(id string) string {

	if len(id) > 64 {
		return id
	}

	str := id

	if isAllDigits(str) {

		// pad numeric identifier to 10 characters with leading zeros
		ln := len(str)
		if ln < PADLENGTH {
			zeros := "0000000000"
			str = zeros[ln:] + str
		}
	}

	return str
}

func createPubMedToPMC() {

	var bldr strings.Builder
	count := 0
	okay := false

	wrtr := bufio.NewWriter(os.Stdout)

	scanr := bufio.NewScanner(os.Stdin)

	// read lines of PMCID link information
	for scanr.Scan() {

		line := scanr.Text()

		if line == "" {
			continue
		}

		cols := strings.Split(line, ",")
		if len(cols) != 2 {
			continue
		}

		fst := cols[0]
		scd := cols[1]

		if fst == "0" || scd == "0" {
			continue
		}

		pdFst := padNumericID(fst)
		pdScd := padNumericID(scd)

		bldr.WriteString(fst + "\tPMID\t" + pdScd + "\n")
		bldr.WriteString(scd + "\tPMCID\t" + pdFst + "\n")

		count++

		if count >= 1000 {
			count = 0
			txt := bldr.String()
			if txt != "" {
				// print current buffer
				wrtr.WriteString(txt[:])
			}
			bldr.Reset()
		}

		okay = true
	}

	if okay {
		txt := bldr.String()
		if txt != "" {
			// print final buffer
			wrtr.WriteString(txt[:])
		}
	}
	bldr.Reset()

	wrtr.Flush()
}

func main() {

	createPubMedToPMC()
}
