// prep-geneinfo.go

// Public domain notice for all NCBI EDirect scripts is located at:
// https://www.ncbi.nlm.nih.gov/books/NBK179288/#chapter6.Public_Domain_Notice

package main

import (
	"bufio"
	"fmt"
	"html"
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

func createGeneInfo() {

	var bldr strings.Builder
	count := 0
	okay := false

	wrtr := bufio.NewWriter(os.Stdout)

	scanr := bufio.NewScanner(os.Stdin)

	// skip first line with column heading names
	for scanr.Scan() {

		line := scanr.Text()
		cols := strings.Split(line, "\t")
		if len(cols) != 16 {
			displayError("Unexpected number of columns (%d) in gene_info.gz", len(cols))
			os.Exit(1)
		}
		if len(cols) != 16 || cols[0] != "#tax_id" {
			displayError("Unrecognized contents in gene_info.gz")
			os.Exit(1)
		}
		break
	}

	bldr.WriteString("<Set>\n")

	// read lines of gene information
	for scanr.Scan() {

		line := scanr.Text()

		cols := strings.Split(line, "\t")
		if len(cols) != 16 {
			continue
		}

		gene := cols[2]
		// skip NEWLINE entries
		if gene == "NEWENTRY" {
			continue
		}

		id := cols[1]
		ltag := cols[3]
		syns := cols[4]
		desc := cols[8]
		auth := cols[10]

		bldr.WriteString("  <Rec>\n")

		bldr.WriteString("    <Id>" + id + "</Id>\n")
		bldr.WriteString("    <Gene>" + html.EscapeString(gene) + "</Gene>\n")

		if ltag != "-" {
			bldr.WriteString("    <Ltag>" + html.EscapeString(ltag) + "</Ltag>\n")
		}
		if syns != "-" {
			bldr.WriteString("    <Syns>" + html.EscapeString(syns) + "</Syns>\n")
		}
		if desc != "-" {
			bldr.WriteString("    <Desc>" + html.EscapeString(desc) + "</Desc>\n")
		}
		if auth != "-" {
			bldr.WriteString("    <Auth>" + html.EscapeString(auth) + "</Auth>\n")
		}

		bldr.WriteString("  </Rec>\n")

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

	bldr.WriteString("</Set>\n")

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

	createGeneInfo()
}
