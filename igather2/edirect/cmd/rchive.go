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
// File Name:  rchive.go
//
// Author:  Jonathan Kans
//
// ==========================================================================

package main

import (
	"bufio"
	"eutils"
	"fmt"
	"github.com/klauspost/pgzip"
	"html"
	"io"
	"maps"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"runtime/pprof"
	"slices"
	"strconv"
	"strings"
	"time"
)

// MAIN FUNCTION

func main() {

	// skip past executable name
	args := os.Args[1:]

	if len(args) < 1 {
		eutils.DisplayError("No command-line arguments supplied to rchive")
		os.Exit(1)
	}

	// print EDirect local archive master and working paths, separated by colon
	if args[0] == "-local" {
		switch len(args) {
		case 1:
			eutils.DisplayError("Missing database argument for rchive -local")
		case 2:
			db := args[1]
			txt := eutils.ReturnArchivePaths(db)
			if txt != "" {
				fmt.Fprintf(os.Stdout, "%s", txt)
			}
		case 3:
			eutils.DisplayError("This use of rchive -local is deprecated")
		default:
			eutils.DisplayError("Too many arguments for rchive -local")
		}
		return
	}

	origArgs := args

	// performance arguments
	chanDepth := 0
	farmSize := 0
	heapSize := 0
	numServe := 0
	goGc := 0

	// processing option arguments
	doCompress := false
	doCleanup := false
	doStrict := false
	doMixed := false
	doSelf := false
	deAccent := false
	deSymbol := false
	doASCII := false
	doStem := false
	deStop := true

	// CONCURRENCY, CLEANUP, AND DEBUGGING FLAGS

	// do these first because -defcpu and -maxcpu can be sent from wrapper before other arguments

	ncpu := max(runtime.NumCPU(), 1)

	// wrapper can limit maximum number of processors to use (undocumented)
	maxProcs := ncpu
	defProcs := 0

	// concurrent performance tuning parameters, can be overridden by -proc and -cons
	numProcs := 0
	serverRatio := 4

	// garbage collector control can be set by environment variable or default value with -gogc 0
	goGc = /* 200 */ 0

	// -flag sets -strict or -mixed cleanup flags from argument
	flgs := ""

	// read data from file instead of stdin
	fileName := ""

	// flag to do incremental indexing and inversion
	e2IndexInvert := false

	// flag for indexed input file
	turbo := false

	// debugging
	mpty := false
	idnt := false
	dbug := false
	stts := false
	timr := false

	// profiling
	prfl := false

	// -transform path can be added early or late in command line
	tform := ""

	// element to use as local data index
	indx := ""

	// file of index values for removing duplicates
	unqe := ""

	// database argument, built-in support for pubmed (default), pmc, and taxonomy
	db := ""

	// pattern argument for incremental indexing
	recname := ""

	// indexing options (e.g., STEM)
	options := ""

	// file with indexing argument lines
	idxargs := ""

	// path for local data indexed as trie
	stsh := ""
	idcs := ""
	incr := ""
	ftch := ""
	strm := ""

	// flag for archiving to selected database
	archive := false

	// XML prefix for deleting a record
	dlete := ""

	// flag for inverted index
	nvrt := false

	// flag for combining sets of inverted files
	join := false

	// destination directory for merging and splitting inverted files
	merg := ""
	isLink := false

	// base destination directory for promoting inverted index to retrieval indices
	prom := ""

	// fields for promoting inverted index files
	fild := ""

	// base for queries
	base := ""

	// query by phrase, normalized terms (with truncation wildcarding)
	phrs := ""
	xact := false
	titl := false
	mtch := false
	mock := false
	btch := false

	// print term list with counts
	trms := ""
	plrl := false
	psns := false

	// link field
	lnks := ""

	ranked := false

	// use gzip compression on local data files
	zipp := false

	// convert UIDs to archive trie
	trei := false

	arcvTrei := false
	invtTrei := false
	pstgTrei := false
	linkTrei := false

	// pad UIDs with leading zeros
	padz := false

	// flag records with damaged embedded HTML tags
	dmgd := false
	dmgdType := ""

	unrecognizedArg := ""

	inSwitch := true

	// get concurrency, cleanup, and debugging flags in any order
	for {

		inSwitch = true
		switch args[0] {

		// concurrency override arguments can be passed in by local wrapper script (undocumented)
		case "-maxcpu":
			maxProcs = eutils.GetNumericArg(args, "Maximum number of processors", 1, 1, ncpu)
			args = args[1:]
		case "-defcpu":
			defProcs = eutils.GetNumericArg(args, "Default number of processors", ncpu, 1, ncpu)
			args = args[1:]
		// performance tuning flags
		case "-proc":
			numProcs = eutils.GetNumericArg(args, "Number of processors", ncpu, 1, ncpu)
			args = args[1:]
		case "-cons":
			serverRatio = eutils.GetNumericArg(args, "Parser to processor ratio", 4, 1, 32)
			args = args[1:]
		case "-serv":
			numServe = eutils.GetNumericArg(args, "Concurrent parser count", 0, 1, 128)
			args = args[1:]
		case "-chan":
			chanDepth = eutils.GetNumericArg(args, "Communication channel depth", 0, ncpu, 128)
			args = args[1:]
		case "-heap":
			heapSize = eutils.GetNumericArg(args, "Unshuffler heap size", 8, 8, 64)
			args = args[1:]
		case "-farm":
			farmSize = eutils.GetNumericArg(args, "Node buffer length", 4, 4, 2048)
			args = args[1:]
		case "-gogc":
			goGc = eutils.GetNumericArg(args, "Garbage collection percentage", 0, 50, 1000)
			args = args[1:]

		// read data from file
		case "-input":
			fileName = eutils.GetStringArg(args, "Input file name")
			args = args[1:]

		// new incremental index and invert function
		case "-e2IndexInvert", "-e2IdxAndInv":
			e2IndexInvert = true
			// should be followed by -transform meshtree.txt -e2index

		// input is indexed with <NEXT_RECORD_SIZE> objects
		case "-turbo":
			turbo = true

		// file with selected indexes for removing duplicates
		case "-unique":
			unqe = eutils.GetStringArg(args, "Unique identifier file")
			args = args[1:]

		// database (currently pubmed, pmc, or taxonomy)
		case "-db":
			db = eutils.GetStringArg(args, "Local archive database")
			db = strings.ToLower(db)
			args = args[1:]

		case "-tag", "-name", "-recname":
			recname = eutils.GetStringArg(args, "Local archive pattern")
			args = args[1:]

		case "-options":
			options = eutils.GetStringArg(args, "Local archive options")
			options = strings.ToLower(options)
			args = args[1:]

		case "-idxargs":
			idxargs = eutils.GetStringArg(args, "File with local archive indexing argument lines")
			args = args[1:]

		// flag for creating archive, needs separate -db argument for database
		case "-archive", "-stash":
			archive = true

		case "-delete":
			dlete = eutils.GetStringArg(args, "XML prefix for deleting a record")
			args = args[1:]

		// local directory path for retrieval
		case "-fetch":
			ftch = eutils.GetStringArg(args, "Fetch path")
			if ftch != "" && !strings.HasSuffix(ftch, "/") {
				ftch += "/"
			}
			args = args[1:]
		// local directory path for retrieval of compressed XML
		case "-stream":
			strm = eutils.GetStringArg(args, "Stream path")
			if strm != "" && !strings.HasSuffix(strm, "/") {
				strm += "/"
			}
			args = args[1:]

		// index transformation file
		case "-transform":
			tform = eutils.GetStringArg(args, "Transformation file")
			args = args[1:]

		// data element for indexing
		case "-index":
			indx = eutils.GetStringArg(args, "Index element")
			args = args[1:]

		// build inverted index
		case "-e2invert":
			nvrt = true

		// combine sets of inverted index files
		case "-join":
			join = true

		case "-fuse":
			// obsolete

		case "-link":
			isLink = true
			// allow -link to immediately precede -merge or -promote
			if len(args) > 1 && !strings.HasPrefix(args[1], "-") {
				lnks = eutils.GetStringArg(args, "Links field")
				args = args[1:]
			}

		// merge inverted index files, distribute by prefix
		case "-mergelink", "-mergeLink", "-merge-link", "-merge-Link":
			isLink = true
			fallthrough
		case "-merge":
			if len(args) < 2 {
				eutils.DisplayError("Merge path is missing")
				os.Exit(1)
			}
			merg = eutils.GetStringArg(args, "Merge field")
			args = args[1:]

		// promote inverted index to term-specific postings files
		case "-promotelink", "-promoteLink", "-promote-link", "-promote-Link":
			isLink = true
			fallthrough
		case "-promote":
			if len(args) < 2 {
				eutils.DisplayError("Promote path and fields are missing")
				os.Exit(1)
			}
			if len(args) < 3 {
				eutils.DisplayError("Promote fields is missing")
				os.Exit(1)
			}
			prom = args[1]
			fild = args[2]
			// skip past first and second arguments
			args = args[2:]

		case "-path":
			base = eutils.GetStringArg(args, "Postings path")
			args = args[1:]

		case "-title":
			titl = true
			fallthrough
		case "-exact":
			xact = true
			fallthrough
		case "-query", "-search":
			phrs = eutils.GetStringArg(args, "Query argument")
			args = args[1:]

		case "-match", "-partial":
			mtch = true
			phrs = eutils.GetStringArg(args, "Match argument")
			args = args[1:]

		case "-ranked":
			ranked = true

		case "-batch":
			btch = true

		case "-mockt":
			titl = true
			fallthrough
		case "-mockx":
			xact = true
			fallthrough
		case "-mock", "-mocks":
			phrs = eutils.GetStringArg(args, "Query argument")
			mock = true
			args = args[1:]

		// -countp tests the files containing positions of terms per UID (undocumented)
		case "-countp":
			psns = true
			fallthrough
		case "-counts":
			plrl = true
			fallthrough
		case "-count", "-countr":
			trms = eutils.GetStringArg(args, "Count argument")
			args = args[1:]

		case "-gzip":
			zipp = true
		case "-inv":
			// obsolete
		case "-trie":
			trei = true
			if len(args) > 1 {
				next := args[1]
				// if next argument is not another flag
				if next != "" && next[0] != '-' {
					// get type of trie
					switch next {
					case "archive", "stash":
						arcvTrei = true
					case "index":
						// obsolete
					case "invert":
						invtTrei = true
					case "posting", "postings":
						pstgTrei = true
					case "link", "links":
						linkTrei = true
					}
					// skip past first of two arguments
					args = args[1:]
				}
			}
		case "-padz":
			padz = true
		// check for missing records

		// use non-threaded fetch function for windows (undocumented)
		case "-windows":
			// now obsolete, ignore command

		// data cleanup flags
		case "-compress", "-compressed":
			doCompress = true
		case "-spaces", "-cleanup":
			doCleanup = true
		case "-strict":
			doStrict = true
		case "-mixed":
			doMixed = true
		case "-self":
			doSelf = true
		case "-accent":
			deAccent = true
		case "-symbol":
			deSymbol = true
		case "-ascii":
			doASCII = true

		// previously visible processing flags (undocumented)
		case "-stems", "-stem":
			doStem = true
		case "-stops", "-stop":
			deStop = false

		case "-unicode":
			// DoUnicode = true
		case "-script":
			// DoScript = true
		case "-mathml":
			// DoMathML = true

		case "-flag", "-flags":
			if len(args) < 2 {
				eutils.DisplayError("-flags argument is missing")
				os.Exit(1)
			}
			flgs = eutils.GetStringArg(args, "Flags argument")
			args = args[1:]

		// debugging flags
		case "-damaged", "-damage", "-broken":
			dmgd = true
			if len(args) > 1 {
				next := args[1]
				// if next argument is not another flag
				if next != "" && next[0] != '-' {
					// get optional extraction class (SELF, SINGLE, DOUBLE, AMPER, or ALL)
					dmgdType = next
					// skip past first of two arguments
					args = args[1:]
				}
			}

		// debugging flags
		case "-debug":
			dbug = true
		case "-stats", "-stat":
			stts = true
		case "-timer":
			timr = true
		case "-profile":
			prfl = true

		default:
			// if not any of the controls, set flag to break out of for loop
			inSwitch = false
			unrecognizedArg = args[0]
		}

		if !inSwitch {
			break
		}

		// skip past argument
		args = args[1:]

		if len(args) < 1 {
			break
		}
	}

	// -flag allows script to set -strict or -mixed (or -stops) from argument
	switch flgs {
	case "strict":
		doStrict = true
	case "mixed":
		doMixed = true
	case "stems", "stem":
		doStem = true
	case "stops", "stop":
		deStop = false
	case "none", "default":
	default:
		if flgs != "" {
			eutils.DisplayError("Unrecognized -flag value '%s'", flgs)
			os.Exit(1)
		}
	}

	/*
		UnicodeFix = ParseMarkup(unicodePolicy, "-unicode")
		ScriptFix = ParseMarkup(scriptPolicy, "-script")
		MathMLFix = ParseMarkup(mathmlPolicy, "-mathml")

		if UnicodeFix != NOMARKUP {
			doUnicode = true
		}

		if ScriptFix != NOMARKUP {
			doScript = true
		}

		if MathMLFix != NOMARKUP {
			doMathML = true
		}
	*/

	if numProcs == 0 {
		if defProcs > 0 {
			numProcs = defProcs
		} else if maxProcs > 0 {
			numProcs = maxProcs
		}
	}
	if numProcs > ncpu {
		numProcs = ncpu
	}
	if numProcs > maxProcs {
		numProcs = maxProcs
	}

	eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, turbo)

	eutils.SetOptions(doStrict, doMixed, doSelf, deAccent, deSymbol, doASCII, doCompress, doCleanup, doStem, deStop)

	// -stats prints number of CPUs and performance tuning values if no other arguments (undocumented)
	if stts && len(args) < 1 {

		eutils.PrintStats()

		return
	}

	// if copying from local files accessed by identifier, add dummy argument to bypass length tests
	if stsh != "" && indx == "" {
		args = append(args, "-dummy")
	} else if ftch != "" || strm != "" {
		args = append(args, "-dummy")
	} else if base != "" {
		args = append(args, "-dummy")
	} else if trei || padz {
		args = append(args, "-dummy")
	}

	// expand -archive ~/ to home directory path
	if stsh != "" {

		if stsh[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				stsh = strings.Replace(stsh, "~/", hom+"/", 1)
			}
		}
	}
	if idcs != "" {

		if idcs[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				idcs = strings.Replace(idcs, "~/", hom+"/", 1)
			}
		}
	}
	if incr != "" {

		if incr[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				incr = strings.Replace(incr, "~/", hom+"/", 1)
			}
		}
	}

	// expand -fetch ~/ to home directory path
	if ftch != "" {

		if ftch[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				ftch = strings.Replace(ftch, "~/", hom+"/", 1)
			}
		}
	}

	// expand -stream ~/ to home directory path
	if strm != "" {

		if strm[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				strm = strings.Replace(strm, "~/", hom+"/", 1)
			}
		}
	}

	// expand -promote ~/ to home directory path
	if prom != "" {

		if prom[:2] == "~/" {
			cur, err := user.Current()
			if err == nil {
				hom := cur.HomeDir
				prom = strings.Replace(prom, "~/", hom+"/", 1)
			}
		}
	}

	// DOCUMENTATION COMMANDS

	if len(args) > 0 {

		inSwitch = true

		switch args[0] {
		case "-version":
			fmt.Printf("%s\n", eutils.EDirectVersion)

		case "-help", "help", "--help":
			eutils.PrintHelp("rchive", "rchive-help.txt")

		case "-extras", "-extra", "-advanced":
			eutils.PrintHelp("rchive", "rchive-extras.txt")
		case "-internal", "-internals":
			eutils.PrintHelp("rchive", "rchive-internal.txt")
		default:
			// if not any of the documentation commands, keep going
			inSwitch = false
		}

		if inSwitch {
			return
		}
	}

	// FILE NAME CAN BE SUPPLIED WITH -input COMMAND TO OVERRIDE stdin DEFAULT

	in := os.Stdin

	// check for data being piped into stdin
	isPipe := false
	fi, staterr := os.Stdin.Stat()
	if staterr == nil {
		isPipe = bool((fi.Mode() & os.ModeNamedPipe) != 0)
	}

	usingFile := false

	if fileName != "" {

		inFile, err := os.Open(fileName)
		if err != nil {
			eutils.DisplayError("Unable to open input file '%s': %s", fileName, err.Error())
			os.Exit(1)
		}

		defer inFile.Close()

		// use indicated file instead of stdin
		in = inFile
		usingFile = true

		if isPipe && runtime.GOOS != "windows" {
			mode := fi.Mode().String()
			eutils.DisplayError("Input data from both stdin and file '%s', mode is '%s'", fileName, mode)
			os.Exit(1)
		}
	}

	// check for -input command after extraction arguments
	for _, str := range args {
		if str == "-input" {
			eutils.DisplayError("Misplaced -input command")
			os.Exit(1)
		}
	}

	// START PROFILING IF REQUESTED

	if prfl {

		f, err := os.Create("cpu.pprof")
		if err != nil {
			eutils.DisplayError("Unable to create profile output file: %s", err.Error())
			os.Exit(1)
		}

		pprof.StartCPUProfile(f)

		defer pprof.StopCPUProfile()
	}

	// INITIALIZE RECORD COUNT

	recordCount := 0
	byteCount := 0

	// print processing rate and program duration
	printDuration := func(name string) {

		eutils.PrintDuration(name, recordCount, byteCount)
	}

	// NAME OF OUTPUT STRING TRANSFORMATION FILE

	transform := make(map[string]string)

	populateTx := func(tf string) {

		inFile, err := os.Open(tf)
		if err != nil {
			eutils.DisplayError("Unable to open transformation file %s", err.Error())
			os.Exit(1)
		}
		defer inFile.Close()

		scanr := bufio.NewScanner(inFile)

		// populate transformation map for -translate (and -matrix) output
		for scanr.Scan() {

			line := scanr.Text()
			frst, scnd := eutils.SplitInTwoLeft(line, "\t")

			transform[frst] = scnd
		}
	}

	// also check for -transform late in command line
	if len(args) > 2 && args[0] == "-transform" {
		tform = args[1]
		args = args[2:]
	}

	if tform != "" {
		populateTx(tform)
	}

	// SPECIFY STRINGS TO GO BEFORE AND AFTER ENTIRE OUTPUT OR EACH RECORD

	head := ""
	tail := ""

	hd := ""
	tl := ""

	parseHeadTail := func() {

		for {

			if len(args) < 1 {
				break
			}

			inSwitch = true

			switch args[0] {
			case "-head":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -head command")
					os.Exit(1)
				}
				head = eutils.ConvertSlash(args[1])
				// allow splitting of -head argument, keep appending until next command (undocumented)
				ofs, nxt := 0, args[2:]
				for {
					if len(nxt) < 1 {
						break
					}
					tmp := nxt[0]
					if strings.HasPrefix(tmp, "-") {
						break
					}
					ofs++
					txt := eutils.ConvertSlash(tmp)
					if head != "" && !strings.HasSuffix(head, "\t") {
						head += "\t"
					}
					head += txt
					nxt = nxt[1:]
				}
				if ofs > 0 {
					args = args[ofs:]
				}
			case "-tail":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -tail command")
					os.Exit(1)
				}
				tail = eutils.ConvertSlash(args[1])
			case "-hd":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -hd command")
					os.Exit(1)
				}
				hd = eutils.ConvertSlash(args[1])
			case "-tl":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -tl command")
					os.Exit(1)
				}
				tl = eutils.ConvertSlash(args[1])
			case "-wrp":
				// shortcut to wrap records in XML tags
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -wrp command")
					os.Exit(1)
				}
				tmp := eutils.ConvertSlash(args[1])
				lft, rgt := eutils.SplitInTwoLeft(tmp, ",")
				if lft != "" {
					head = "<" + lft + ">"
					tail = "</" + lft + ">"
				}
				if rgt != "" {
					hd = "<" + rgt + ">"
					tl = "</" + rgt + ">"
				}
			case "-set":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -set command")
					os.Exit(1)
				}
				tmp := eutils.ConvertSlash(args[1])
				if tmp != "" {
					head = "<" + tmp + ">"
					tail = "</" + tmp + ">"
				}
			case "-rec":
				if len(args) < 2 {
					eutils.DisplayError("Pattern missing after -rec command")
					os.Exit(1)
				}
				tmp := eutils.ConvertSlash(args[1])
				if tmp != "" {
					hd = "<" + tmp + ">"
					tl = "</" + tmp + ">"
				}
			default:
				// if not any of the controls, set flag to break out of for loop
				inSwitch = false
			}

			if !inSwitch {
				break
			}

			// skip past arguments
			args = args[2:]

			if len(args) < 1 {
				eutils.DisplayError("Insufficient command-line arguments supplied to rchive")
				os.Exit(1)
			}
		}
	}

	// -e2IndexInvert FOLLOWED BY -transform meshtree.txt AND -e2index

	if e2IndexInvert && len(args) > 0 && args[0] == "-e2index" {

		// skip past command name
		args = args[1:]

		// environment variable can override garbage collector (undocumented)
		gcEnv := os.Getenv("EDIRECT_INDEX_GOGC")
		if gcEnv != "" {
			val, err := strconv.Atoi(gcEnv)
			if err == nil {
				if val >= 50 && val <= 1000 {
					debug.SetGCPercent(val)
				} else {
					debug.SetGCPercent(100)
				}
			}
		}

		// environment variable can override number of servers (undocumented)
		svEnv := os.Getenv("EDIRECT_INDEX_SERV")
		if svEnv != "" {
			val, err := strconv.Atoi(svEnv)
			if err == nil {
				if val >= 1 && val <= 128 {
					numServe = val
				} else {
					numServe = 1
				}
				eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, false)
			}
		}

		res := eutils.MakeE2Commands(tform, idxargs)

		// data in pipe, so replace arguments, execute dynamically
		args = res

		if len(args) < 1 {
			eutils.DisplayError("-e2IndexInvert argument generation failure")
			os.Exit(1)
		}

		// parse new -head, -tail, etc.
		parseHeadTail()

		// parse expected -e2index generated arguments
		cmds := eutils.ParseArguments(args, recname)
		if cmds == nil {
			eutils.DisplayError("Problem parsing -e2index arguments after -e2IndexInvert")
			os.Exit(1)
		}

		callConsumers := func(inp <-chan eutils.XMLRecord) <-chan eutils.XMLRecord {

			// closure allows access to unchanging cmds and transform arguments
			return eutils.CreateXMLConsumers(cmds, "", "<IdxDocument>", "</IdxDocument>", transform, false, nil, inp)
		}

		e2iq := eutils.IndexAndInvertArchive(db, recname, callConsumers)
		if e2iq == nil {
			eutils.DisplayError("Unable to create indexer/inverter channel")
			os.Exit(1)
		}

		// drain channel for names of folder-specific inverted index files that were updated
		for range /* itm := */ e2iq {
			recordCount++
			// fmt.Fprintf(os.Stdout, "%s\n", itm)
		}

		// fmt.Fprintf(os.Stdout, "\n")

		if timr {
			printDuration("files")
		}

		return
	}

	// -e2incIndex FOLLOWED BY -transform meshtree.txt AND -e2index (obsolete)

	// -e2incInvert (obsolete)

	// -e2index PROCESSING OF XML RECORDS

	if len(args) > 0 && args[0] == "-e2index" {

		// e.g., rchive -transform [meshtree.txt] -e2index

		// skip past command name
		args = args[1:]

		// environment variable can override garbage collector (undocumented)
		gcEnv := os.Getenv("EDIRECT_INDEX_GOGC")
		if gcEnv != "" {
			val, err := strconv.Atoi(gcEnv)
			if err == nil {
				if val >= 50 && val <= 1000 {
					debug.SetGCPercent(val)
				} else {
					debug.SetGCPercent(100)
				}
			}
		}

		// environment variable can override number of servers (undocumented)
		svEnv := os.Getenv("EDIRECT_INDEX_SERV")
		if svEnv != "" {
			val, err := strconv.Atoi(svEnv)
			if err == nil {
				if val >= 1 && val <= 128 {
					numServe = val
				} else {
					numServe = 1
				}
				eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, turbo)
			}
		}

		res := eutils.MakeE2Commands(tform, idxargs)

		if !isPipe && !usingFile {
			// no piped input, so write output instructions
			fmt.Printf("rchive")
			if tform != "" {
				fmt.Printf(" -transform %s", tform)
			}
			for _, str := range res {
				if strings.HasPrefix(str, "-") {
					fmt.Printf(" %s", str)
				} else {
					fmt.Printf(" \"%s\"", str)
				}
			}
			fmt.Printf("\n")
			return
		}

		// data in pipe, so replace arguments, execute dynamically
		args = res

		if len(args) < 1 {
			eutils.DisplayError("-e2index argument generation failure")
			os.Exit(1)
		}

		// parse new -head, -tail, etc.
		parseHeadTail()

		// parse expected -e2index generated arguments
		cmds := eutils.ParseArguments(args, recname)
		if cmds == nil {
			eutils.DisplayError("Problem parsing -e2index arguments")
			os.Exit(1)
		}

		rdr := eutils.CreateXMLStreamer(in, nil)

		if rdr == nil {
			eutils.DisplayError("Unable to create XML Block Reader")
			os.Exit(1)
		}

		// launch producer goroutine to partition XML by pattern
		xmlq := eutils.CreateXMLProducer(recname, "", false, rdr)

		// launch consumer goroutines to parse and explore partitioned XML objects
		tblq := eutils.CreateXMLConsumers(cmds, "", "<IdxDocument>", "</IdxDocument>", transform, false, nil, xmlq)

		// launch unshuffler goroutine to restore order of results
		unsq := eutils.CreateXMLUnshuffler(tblq)

		if xmlq == nil || tblq == nil || unsq == nil {
			eutils.DisplayError("Unable to create servers")
			os.Exit(1)
		}

		recordCount, byteCount = eutils.DrainExtractions(head, tail, "", mpty, idnt, nil, unsq)

		if timr {
			printDuration("records")
		}

		return
	}

	// PRINT ALL TERMS IN A FIELD

	if len(args) > 1 && args[0] == "-terms" {

		// skip past command name
		args = args[1:]

		fld := args[0]

		trms := eutils.StreamTerms(db, fld)
		if trms == nil {
			eutils.DisplayError("Unable to create term generator")
			os.Exit(1)
		}

		for str := range trms {
			// returning multi-line strings, no need for trailing newline
			fmt.Fprintf(os.Stdout, "%s", str)
		}

		return
	}

	// PRINT ALL TERMS AND COUNTS IN A FIELD

	if len(args) > 1 && args[0] == "-totals" {

		// skip past command name
		args = args[1:]

		fld := args[0]

		trms := eutils.StreamTotals(db, fld)
		if trms == nil {
			eutils.DisplayError("Unable to create totals generator")
			os.Exit(1)
		}

		for str := range trms {
			// returning multi-line strings, no need for trailing newline
			fmt.Fprintf(os.Stdout, "%s", str)
		}

		return
	}

	// -join combines subsets of inverted files for subsequent -merge operation
	if join {

		// environment variable can override garbage collector (undocumented)
		gcEnv := os.Getenv("EDIRECT_JOIN_GOGC")
		if gcEnv != "" {
			val, err := strconv.Atoi(gcEnv)
			if err == nil {
				if val >= 50 && val <= 1000 {
					debug.SetGCPercent(val)
				} else {
					debug.SetGCPercent(100)
				}
			}
		}

		// environment variable can override number of servers (undocumented)
		svEnv := os.Getenv("EDIRECT_JOIN_SERV")
		if svEnv != "" {
			val, err := strconv.Atoi(svEnv)
			if err == nil {
				if val >= 1 && val <= 128 {
					numServe = val
				} else {
					numServe = 1
				}
				eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, false)
			}
		}

		plexToGzFile := func(zipp bool, inp <-chan eutils.Plex) <-chan string {

			if inp == nil {
				return nil
			}

			fin := make(chan string, chanDepth)
			if fin == nil {
				eutils.DisplayError("Unable to create saveToGzFile channel")
				os.Exit(1)
			}

			const chunkSize = 100000

			go func(zipp bool, inp <-chan eutils.Plex, fin chan<- string) {

				defer close(fin)

				var out io.Writer

				out = os.Stdout

				if zipp {

					zpr, err := pgzip.NewWriterLevel(out, pgzip.BestSpeed)
					if err != nil {
						eutils.DisplayError("Unable to create compressor: %s", err.Error())
						os.Exit(1)
					}

					// close decompressor when all records have been processed
					defer zpr.Close()

					// use compressor for writing file
					out = zpr
				}

				// create buffered writer layer
				wrtr := bufio.NewWriter(out)

				wrtr.WriteString("<InvDocumentSet>\n")

				currSize := 0

				// drain channel of alphabetized results
				for curr := range inp {

					str := curr.Text
					if str == "" {
						continue
					}

					// send result to output
					wrtr.WriteString(str)

					recordCount++

					currSize += len(str)
					if currSize > chunkSize {
						wrtr.Flush()
					}
				}

				wrtr.WriteString("</InvDocumentSet>\n\n")

				wrtr.Flush()

				fin <- "done"
			}(zipp, inp, fin)

			return fin
		}

		chns := eutils.CreatePresenters(args)
		mfld := eutils.CreateManifold(chns)
		pgzq := plexToGzFile(zipp, mfld)

		if chns == nil || mfld == nil || pgzq == nil {
			eutils.DisplayError("Unable to create inverted index joiner")
			os.Exit(1)
		}

		for range pgzq {
			_ = <-pgzq
		}

		if timr {
			printDuration("terms")
		}

		return
	}

	// MERGE INVERTED INDEX FILES AND GROUP BY TERM

	// -merge combines inverted files, distributes by prefix
	if merg != "" {

		// environment variable can override garbage collector (undocumented)
		gcEnv := os.Getenv("EDIRECT_MERGE_GOGC")
		if gcEnv != "" {
			val, err := strconv.Atoi(gcEnv)
			if err == nil {
				if val >= 50 && val <= 1000 {
					debug.SetGCPercent(val)
				} else {
					debug.SetGCPercent(100)
				}
			}
		}

		// environment variable can override number of servers (undocumented)
		svEnv := os.Getenv("EDIRECT_MERGE_SERV")
		if svEnv != "" {
			val, err := strconv.Atoi(svEnv)
			if err == nil {
				if val >= 1 && val <= 128 {
					numServe = val
				} else {
					numServe = 1
				}
				eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, false)
			}
		}

		chns := eutils.CreatePresenters(args)
		mfld := eutils.CreateManifold(chns)
		sptr := eutils.CreateSplitter(merg, db, zipp, isLink, mfld)

		if chns == nil || mfld == nil || sptr == nil {
			eutils.DisplayError("Unable to create inverted index merger")
			os.Exit(1)
		}

		// drain channel, print two-to-four-character index name
		startTime := time.Now()
		first := true
		col := 0
		spaces := "       "

		for str := range sptr {

			stopTime := time.Now()
			duration := stopTime.Sub(startTime)
			seconds := float64(duration.Nanoseconds()) / 1e9

			if timr {
				if first {
					first = false
				} else {
					fmt.Fprintf(os.Stdout, "%.3f\n", seconds)
				}
				fmt.Fprintf(os.Stdout, "%s\t", str)
			} else {
				blank := 7 - len(str)
				if blank > 0 {
					fmt.Fprintf(os.Stdout, "%s", spaces[:blank])
				}
				fmt.Fprintf(os.Stdout, "%s", str)
				col++
				if col >= 10 {
					col = 0
					fmt.Fprintf(os.Stdout, "\n")
				}
			}

			recordCount++

			startTime = time.Now()
		}

		stopTime := time.Now()
		duration := stopTime.Sub(startTime)
		seconds := float64(duration.Nanoseconds()) / 1e9

		if timr {
			fmt.Fprintf(os.Stdout, "%.3f\n", seconds)
		} else if col > 0 {
			fmt.Fprintf(os.Stdout, "\n")
		}

		if timr {
			printDuration("groups")
		}

		return
	}

	// PROMOTE MERGED INVERTED INDEX TO TERM LIST AND POSTINGS FILES

	if prom != "" && fild != "" {

		prmq := eutils.CreatePromoters(prom, db, fild, isLink, args)

		if prmq == nil {
			eutils.DisplayError("Unable to create new postings file generator")
			os.Exit(1)
		}

		col := 0
		spaces := "       "

		// drain channel, print 2-4 character file prefix
		for str := range prmq {

			blank := 7 - len(str)
			if blank > 0 {
				fmt.Fprintf(os.Stdout, "%s", spaces[:blank])
			}
			fmt.Fprintf(os.Stdout, "%s", str)
			col++
			if col >= 10 {
				col = 0
				fmt.Fprintf(os.Stdout, "\n")
			}

			recordCount++
		}

		if col > 0 {
			fmt.Fprintf(os.Stdout, "\n")
		}

		if timr {
			printDuration("terms")
		}

		return
	}

	// QUERY POSTINGS FILES

	if btch {

		// read query lines for exact match
		scanr := bufio.NewScanner(in)

		for scanr.Scan() {
			txt := scanr.Text()

			// deStop should match value used in building the indices
			recordCount += eutils.ProcessSearch(db, txt, true, false, false, deStop)
		}

		if timr {
			printDuration("records")
		}

		return
	}

	if phrs != "" {

		// deStop should match value used in building the indices
		if mock {
			recordCount = eutils.ProcessMock(db, phrs, xact, titl, deStop)
		} else if mtch {
			eutils.ProcessMatch(db, phrs, deStop)
		} else {
			recordCount = eutils.ProcessSearch(db, phrs, xact, titl, false, deStop)
		}

		if timr {
			printDuration("records")
		}

		return
	}

	if lnks != "" && merg == "" && prom == "" {

		eutils.ProcessLinks(db, lnks, ranked)

		if timr {
			printDuration("terms")
		}

		return
	}

	if trms != "" {

		// deStop should match value used in building the indices
		recordCount = eutils.ProcessCount(db, trms, plrl, psns, deStop)

		if timr {
			printDuration("terms")
		}

		return
	}

	// CONFIRM INPUT DATA AVAILABILITY AFTER RUNNING COMMAND GENERATORS

	if fileName == "" && runtime.GOOS != "windows" {

		fromStdin := bool((fi.Mode() & os.ModeCharDevice) == 0)
		if !isPipe || !fromStdin {
			mode := fi.Mode().String()
			if unrecognizedArg != "" {
				eutils.DisplayError("No data supplied to stdin or file, mode: '%s', unrecognized argument: '%s'",
					mode, unrecognizedArg)
			} else {
				eutils.DisplayError("No data supplied from stdin or file, mode: '%s'", mode)
			}
			os.Exit(1)
		}
	}

	if !usingFile && !isPipe {

		eutils.DisplayError("No XML input data supplied to rchive")
		os.Exit(1)
	}

	// SPECIFY STRINGS TO GO BEFORE AND AFTER ENTIRE OUTPUT OR EACH RECORD

	parseHeadTail()

	// PAD IDENTIFIER WITH LEADING ZEROS

	if padz {

		scanr := bufio.NewScanner(in)

		// read lines of identifiers
		for scanr.Scan() {

			str := scanr.Text()

			str = eutils.PadNumericID(str)

			os.Stdout.WriteString(str)
			os.Stdout.WriteString("\n")
		}

		return
	}

	// PRINT ARCHIVE SUBPATH FROM IDENTIFIER

	// -trie converts identifier to directory subpath plus file name (undocumented)
	if trei {

		scanr := bufio.NewScanner(in)

		sfx := ".xml"
		if invtTrei {
			sfx = ".inv"
		} else if pstgTrei {
			sfx = ""
		}

		printTrie := func(dir, id, sfx string) {

			if id == "" || dir == "" {
				return
			}

			if zipp {
				sfx += ".gz"
			}

			fpath := filepath.Join(dir, id+sfx)
			if fpath == "" {
				return
			}

			os.Stdout.WriteString(fpath)
			os.Stdout.WriteString("\n")
		}

		// read lines of identifiers
		for scanr.Scan() {

			file := scanr.Text()
			if file == "" {
				continue
			}

			dir := ""
			id := ""

			if arcvTrei {
				dir, id = eutils.ArchiveTrie(file)
				printTrie(dir, id, sfx)
			} else if invtTrei {
				dir, id = eutils.InvertTrie(file)
				printTrie(dir, id, sfx)
			} else if pstgTrei {
				dir, id = eutils.PostingsTrie(file, "")
				printTrie(dir, id, sfx)
			} else if linkTrei {
				dir, id = eutils.LinksTrie(file, true)
				printTrie(dir, id, sfx)
			} else {
				os.Stdout.WriteString(file)
				os.Stdout.WriteString("\n")

				os.Stdout.WriteString(eutils.PadNumericID(file))
				os.Stdout.WriteString("\n")

				dir, id = eutils.ArchiveTrie(file)
				printTrie(dir, id, ".archive")

				dir, id = eutils.InvertTrie(file)
				printTrie(dir, id, ".inv")

				os.Stdout.WriteString("\n")
			}
		}

		return
	}

	// RETRIEVE XML COMPONENT RECORDS FROM LOCAL DIRECTORY INDEXED ARCHIVE

	// -fetch without -index retrieves uncompressed XML records from indexed archive files
	if ftch != "" && indx == "" {

		uidq := eutils.UIDStreamer(in)
		rdaq := eutils.ReadArchiveRecords(db, turbo, uidq)

		if uidq == nil || rdaq == nil {
			eutils.DisplayError("Unable to create archive reader")
			os.Exit(1)
		}

		if head != "" {
			os.Stdout.WriteString(head)
			os.Stdout.WriteString("\n")
		}

		// drain output channel
		for str := range rdaq {

			if str == "" {
				continue
			}

			if hd != "" {
				os.Stdout.WriteString(hd)
				os.Stdout.WriteString("\n")
			}

			// send result to output
			newln := false
			if !strings.HasSuffix(str, "\n") {
				newln = true
			}

			os.Stdout.WriteString(str)
			if newln {
				os.Stdout.WriteString("\n")
			}

			if tl != "" {
				os.Stdout.WriteString(tl)
				os.Stdout.WriteString("\n")
			}

			recordCount++
		}

		if tail != "" {
			os.Stdout.WriteString(tail)
			os.Stdout.WriteString("\n")
		}

		if timr {
			printDuration("records")
		}

		return
	}

	// -stream without -index retrieves compressed XML records from indexed archive files
	if strm != "" && indx == "" {

		uidq := eutils.UIDStreamer(in)
		staq := eutils.StreamArchiveRecords(db, uidq)

		if uidq == nil || staq == nil {
			eutils.DisplayError("Unable to create archive reader")
			os.Exit(1)
		}

		// drain output channel
		for data := range staq {

			if data == nil {
				continue
			}

			recordCount++

			_, err := os.Stdout.Write(data)
			if err != nil {
				fmt.Fprintf(os.Stderr, "%s\n", err.Error())
			}
		}

		if timr {
			printDuration("records")
		}

		return
	}

	// ENTREZ INDEX INVERSION

	// -e2invert reads IdxDocumentSet XML and creates an inverted index
	if nvrt {

		// environment variable can override garbage collector (undocumented)
		gcEnv := os.Getenv("EDIRECT_INVERT_GOGC")
		if gcEnv != "" {
			val, err := strconv.Atoi(gcEnv)
			if err == nil {
				if val >= 50 && val <= 1000 {
					debug.SetGCPercent(val)
				} else {
					debug.SetGCPercent(100)
				}
			}
		}

		// environment variable can override number of servers (undocumented)
		svEnv := os.Getenv("EDIRECT_INVERT_SERV")
		if svEnv != "" {
			val, err := strconv.Atoi(svEnv)
			if err == nil {
				if val >= 1 && val <= 128 {
					numServe = val
				} else {
					numServe = 1
				}
				eutils.SetTunings(numProcs, numServe, serverRatio, chanDepth, farmSize, heapSize, goGc, false)
			}
		}

		byt, err := io.ReadAll(in)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s\n", err.Error())
			return
		}

		str := string(byt)
		if str == "" {
			return
		}

		if !strings.HasSuffix(str, "\n") {
			str += "\n"
		}

		colq := eutils.StringToChan(str)
		iifq := eutils.InvertIndexedFile(colq, nil)

		if colq == nil || iifq == nil {
			eutils.DisplayError("Unable to create inverter")
			os.Exit(1)
		}

		var out io.Writer

		out = os.Stdout

		if zipp {

			zpr, err := pgzip.NewWriterLevel(out, pgzip.BestSpeed)
			if err != nil {
				eutils.DisplayError("Unable to create compressor: %s", err.Error())
				os.Exit(1)
			}

			// close decompressor when all records have been processed
			defer zpr.Close()

			// use compressor for writing file
			out = zpr
		}

		// create buffered writer layer
		wrtr := bufio.NewWriter(out)

		wrtr.WriteString("<InvDocumentSet>\n")

		// drain channel of alphabetized results
		for str := range iifq {

			// send result to output
			wrtr.WriteString(str)

			recordCount++
		}

		wrtr.WriteString("</InvDocumentSet>\n\n")

		wrtr.Flush()

		if timr {
			printDuration("terms")
		}

		return
	}

	// CREATE XML BLOCK READER FROM STDIN OR FILE FOR SUBSEQUENT FUNCTIONS

	rdr := eutils.CreateXMLStreamer(in, nil)
	if rdr == nil {
		eutils.DisplayError("Unable to create XML Block Reader")
		os.Exit(1)
	}

	// ENSURE PRESENCE OF PATTERN ARGUMENT FOR SUBSEQUENT FUNCTIONS

	if len(args) < 1 {
		eutils.DisplayError("Insufficient command-line arguments supplied to rchive")
		os.Exit(1)
	}

	// allow -record as synonym of -pattern (undocumented)
	if args[0] == "-record" || args[0] == "-Record" {
		args[0] = "-pattern"
	}

	// make sure top-level -pattern command is next
	if args[0] != "-pattern" && args[0] != "-Pattern" {
		eutils.DisplayError("No -pattern in command-line arguments")
		os.Exit(1)
	}
	if len(args) < 2 {
		eutils.DisplayError("Item missing after -pattern command")
		os.Exit(1)
	}

	topPat := args[1]
	if topPat == "" {
		eutils.DisplayError("Item missing after -pattern command")
		os.Exit(1)
	}
	if strings.HasPrefix(topPat, "-") {
		eutils.DisplayError("Misplaced %s command", topPat)
		os.Exit(1)
	}

	// look for -pattern Parent/* construct for heterogeneous data, e.g., -pattern PubmedArticleSet/*
	topPattern, star := eutils.SplitInTwoLeft(topPat, "/")
	if topPattern == "" {
		return
	}

	parent := ""
	if star == "*" {
		parent = topPattern
	} else if star != "" {
		eutils.DisplayError("-pattern Parent/Child construct is not supported")
		os.Exit(1)
	}

	// REPORT RECORDS THAT CONTAIN DAMAGED EMBEDDED HTML TAGS

	reportEncodedMarkup := func(typ, id, str string) {

		var buffer strings.Builder

		max := len(str)

		lookAhead := func(txt string, to int) string {

			mx := len(txt)
			if to > mx {
				to = mx
			}
			pos := strings.Index(txt[:to], "gt;")
			if pos > 0 {
				to = pos + 3
			}
			return txt[:to]
		}

		findContext := func(fr, to int) string {

			numSpaces := 0

			for fr > 0 {
				ch := str[fr]
				if ch == ' ' {
					numSpaces++
					if numSpaces > 1 {
						fr++
						break
					}
				} else if ch == '\n' || ch == '>' {
					fr++
					break
				}
				fr--
			}

			numSpaces = 0

			for to < max {
				ch := str[to]
				if ch == ' ' {
					numSpaces++
					if numSpaces > 1 {
						break
					}
				} else if ch == '\n' || ch == '<' {
					break
				}
				to++
			}

			return str[fr:to]
		}

		reportMarkup := func(lbl string, fr, to int, txt string) {

			if lbl == typ || typ == "ALL" {
				// extract XML of SELF, SINGLE, DOUBLE, or AMPER types, or ALL
				buffer.WriteString(str)
				buffer.WriteString("\n")
			} else if typ == "" {
				// print report
				buffer.WriteString(id)
				buffer.WriteString("\t")
				buffer.WriteString(lbl)
				buffer.WriteString("\t")
				buffer.WriteString(txt)
				buffer.WriteString("\t| ")
				ctx := findContext(fr, to)
				buffer.WriteString(ctx)
				if eutils.HasUnicodeMarkup(ctx) {
					ctx = eutils.RepairUnicodeMarkup(ctx, eutils.SPACE)
				}
				ctx = eutils.CleanupEncodedMarkup(ctx)
				buffer.WriteString("\t| ")
				buffer.WriteString(ctx)
				if eutils.HasAmpOrNotASCII(ctx) {
					ctx = html.UnescapeString(ctx)
				}
				buffer.WriteString("\t| ")
				buffer.WriteString(ctx)
				buffer.WriteString("\n")
			}
		}

		/*
			badTags := [10]string{
				"<i/>",
				"<i />",
				"<b/>",
				"<b />",
				"<u/>",
				"<u />",
				"<sup/>",
				"<sup />",
				"<sub/>",
				"<sub />",
			}
		*/

		skip := 0

		/*
			var prev rune
		*/

		for i, ch := range str {
			if skip > 0 {
				skip--
				continue
			}
			/*
				if ch > 127 {
					if IsUnicodeSuper(ch) {
						if IsUnicodeSubsc(prev) {
							// reportMarkup("UNIUP", i, i+2, string(ch))
						}
					} else if IsUnicodeSubsc(ch) {
						if IsUnicodeSuper(prev) {
							// reportMarkup("UNIDN", i, i+2, string(ch))
						}
					} else if ch == '\u0038' || ch == '\u0039' {
						// reportMarkup("ANGLE", i, i+2, string(ch))
					}
					prev = ch
					continue
				} else {
					prev = ' '
				}
			*/
			if ch == '<' {
				/*
					j := i + 1
					if j < max {
						nxt := str[j]
						if nxt == 'i' || nxt == 'b' || nxt == 'u' || nxt == 's' {
							for _, tag := range badTags {
								if strings.HasPrefix(str, tag) {
									k := len(tag)
									reportMarkup("SELF", i, i+k, tag)
									break
								}
							}
						}
					}
					if strings.HasPrefix(str[i:], "</sup><sub>") {
						// reportMarkup("SUPSUB", i, i+11, "</sup><sub>")
					} else if strings.HasPrefix(str[i:], "</sub><sup>") {
						// reportMarkup("SUBSUP", i, i+11, "</sub><sup>")
					}
				*/
				continue
			} else if ch != '&' {
				continue
			} else if strings.HasPrefix(str[i:], "&lt;") {
				sub := lookAhead(str[i:], 14)
				_, ok := eutils.HTMLRepair(sub)
				if ok {
					skip = len(sub) - 1
					reportMarkup("SINGLE", i, i+skip+1, sub)
					continue
				}
			} else if strings.HasPrefix(str[i:], "&amp;lt;") {
				sub := lookAhead(str[i:], 22)
				_, ok := eutils.HTMLRepair(sub)
				if ok {
					skip = len(sub) - 1
					reportMarkup("DOUBLE", i, i+skip+1, sub)
					continue
				}
			} else if strings.HasPrefix(str[i:], "&amp;amp;") {
				reportMarkup("AMPER", i, i+9, "&amp;amp;")
				skip = 8
				continue
			}
		}

		res := buffer.String()

		os.Stdout.WriteString(res)
	}

	// -damaged plus -index plus -pattern reports records with multiply-encoded HTML tags
	if dmgd && indx != "" {

		find := eutils.ParseIndex(indx)

		eutils.PartitionXML(topPattern, star, false, rdr,
			func(str string) {
				recordCount++

				id := eutils.FindIdentifier(str[:], parent, find)
				if id == "" {
					return
				}

				// remove default version suffix
				if strings.HasSuffix(id, ".1") {
					idlen := len(id)
					id = id[:idlen-2]
				}

				reportEncodedMarkup(dmgdType, id, str)
			})

		if timr {
			printDuration("records")
		}

		return
	}

	// SAVE XML COMPONENT RECORDS TO LOCAL DIRECTORY INDEXED ARCHIVE

	// -archive plus -index plus -pattern saves XML files to indexed local archive
	if archive && db != "" && indx != "" {

		xmlq := eutils.CreateXMLProducer(topPattern, star, false, rdr)
		stsq := eutils.WriteArchive(db, topPattern, indx, dlete, xmlq)

		if xmlq == nil || stsq == nil {
			eutils.DisplayError("Unable to create stash generator")
			os.Exit(1)
		}

		// drain output channel
		for str := range stsq {

			if dbug {
				fmt.Fprintf(os.Stderr, "%s\n", str)
			}

			recordCount++
		}

		if timr {
			printDuration("records")
		}

		return
	}

	// READ FILE OF IDENTIFIERS AND EXTRACT SELECTED RECORDS FROM XML INPUT FILE

	// -index plus -unique [plus -head/-tail/-hd/-tl] plus -pattern with no other extraction arguments
	// takes an XML input file and a file of its UIDs and keeps only the last version of each record
	if indx != "" && unqe != "" && len(args) == 2 {

		// read file of identifiers to use for filtering
		fl, err := os.Open(unqe)
		if err != nil {
			eutils.DisplayError("Unable to open identifier file '%s': %s", unqe, err.Error())
			os.Exit(1)
		}

		// create map that counts instances of each UID
		order := make(map[string]int)

		scanr := bufio.NewScanner(fl)

		// read lines of identifiers
		for scanr.Scan() {

			id := scanr.Text()

			// map records count for given identifier
			val := order[id]
			val++
			order[id] = val
		}

		fl.Close()

		find := eutils.ParseIndex(indx)

		if head != "" {
			os.Stdout.WriteString(head)
			os.Stdout.WriteString("\n")
		}

		eutils.PartitionXML(topPattern, star, false, rdr,
			func(str string) {
				recordCount++

				id := eutils.FindIdentifier(str[:], parent, find)
				if id == "" {
					return
				}

				val, ok := order[id]
				if !ok {
					// not in identifier list, skip
					return
				}
				// decrement count in map
				val--
				order[id] = val
				if val > 0 {
					// only write last record with a given identifier
					return
				}

				if hd != "" {
					os.Stdout.WriteString(hd)
					os.Stdout.WriteString("\n")
				}

				// write selected record
				os.Stdout.WriteString(str[:])
				os.Stdout.WriteString("\n")

				if tl != "" {
					os.Stdout.WriteString(tl)
					os.Stdout.WriteString("\n")
				}
			})

		if tail != "" {
			os.Stdout.WriteString(tail)
			os.Stdout.WriteString("\n")
		}

		if timr {
			printDuration("records")
		}

		return
	}

	// GENERATE RECORD INDEX ON XML INPUT FILE

	// -index plus -pattern prints record identifier and XML size
	if indx != "" {

		lbl := ""
		// check for optional filename label after -pattern argument (undocumented)
		if len(args) > 3 && args[2] == "-lbl" {
			lbl = args[3]

			lbl = strings.TrimSpace(lbl)
			if strings.HasPrefix(lbl, "pubmed") {
				lbl = lbl[7:]
			}
			if strings.HasSuffix(lbl, ".xml.gz") {
				xlen := len(lbl)
				lbl = lbl[:xlen-7]
			}
			lbl = strings.TrimSpace(lbl)
		}

		// legend := "ID\tREC\tSIZE"

		find := eutils.ParseIndex(indx)

		eutils.PartitionXML(topPattern, star, false, rdr,
			func(str string) {
				recordCount++

				id := eutils.FindIdentifier(str[:], parent, find)
				if id == "" {
					return
				}
				if lbl != "" {
					fmt.Printf("%s\t%d\t%s\n", id, len(str), lbl)
				} else {
					fmt.Printf("%s\t%d\n", id, len(str))
				}
			})

		if timr {
			printDuration("records")
		}

		return
	}

	// SORT XML RECORDS BY IDENTIFIER

	// -pattern record_name -sort parent/element@attribute^version, strictly alphabetic sort order (undocumented)
	if len(args) == 4 && args[2] == "-sort" {

		indx := args[3]

		// create map that records each UID
		order := make(map[string][]string)

		find := eutils.ParseIndex(indx)

		eutils.PartitionXML(topPattern, star, false, rdr,
			func(str string) {
				recordCount++

				id := eutils.FindIdentifier(str[:], parent, find)
				if id == "" {
					return
				}

				data, ok := order[id]
				if !ok {
					data = make([]string, 0, 1)
				}
				data = append(data, str)
				// always need to update order, since data may be reallocated
				order[id] = data
			})

		// sort fields in alphabetical order, unlike xtract version, which sorts numbers by numeric value
		keys := slices.Sorted(maps.Keys(order))

		if head != "" {
			os.Stdout.WriteString(head)
			os.Stdout.WriteString("\n")
		}

		for _, id := range keys {

			strs := order[id]
			for _, str := range strs {
				os.Stdout.WriteString(str)
				os.Stdout.WriteString("\n")
			}
		}

		if tail != "" {
			os.Stdout.WriteString(tail)
			os.Stdout.WriteString("\n")
		}

		if timr {
			printDuration("records")
		}

		return
	}

	// REPORT UNRECOGNIZED COMMAND

	eutils.DisplayError("Unrecognized rchive command")

	for _, str := range origArgs {
		fmt.Fprintf(os.Stderr, "%s\n", str)
	}
	fmt.Fprintf(os.Stderr, "\n")

	os.Exit(1)
}
