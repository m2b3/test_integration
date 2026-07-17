#!/bin/bash

# ===========================================================================
#
#                            PUBLIC DOMAIN NOTICE
#            National Center for Biotechnology Information (NCBI)
#
#  This software/database is a "United States Government Work" under the
#  terms of the United States Copyright Act.  It was written as part of
#  the author's official duties as a United States Government employee and
#  thus cannot be copyrighted.  This software/database is freely available
#  to the public for use. The National Library of Medicine and the U.S.
#  Government do not place any restriction on its use or reproduction.
#  We would, however, appreciate having the NCBI and the author cited in
#  any work or product based on this material.
#
#  Although all reasonable efforts have been taken to ensure the accuracy
#  and reliability of the software and data, the NLM and the U.S.
#  Government do not and cannot warrant the performance or results that
#  may be obtained by using this software or data. The NLM and the U.S.
#  Government disclaim all warranties, express or implied, including
#  warranties of performance, merchantability or fitness for any particular
#  purpose.
#
# ===========================================================================
#
# File Name:  xcommon.sh
#
# Author:  Jonathan Kans, Aaron Ucko
#
# Version Creation Date:   01/18/2025
#
# ==========================================================================

# environment variable turns on shell tracing

if [ -n "${EDIRECT_TRACE}" ] && [ "${EDIRECT_TRACE}" = true ]
then
  set -x
fi

# initialize common flags

dbase=""

ids=""
rest=""
input=""
num=""

mssg=""
web_env=""
qry_key=""
qury=""

reldate=""
mindate=""
maxdate=""
datetype=""

archiveBase=""
sentinelsBase=""
dataBase=""
postingsBase=""
sourceBase=""
extrasBase=""
invertBase=""
collectBase=""
mergedBase=""
scratchBase=""
currentBase=""
indexedBase=""
invertedBase=""
temporaryBase=""

localPaths=""

osname=$( uname -s | sed -e 's/_NT-.*$/_NT/; s/^MINGW[0-9]*/CYGWIN/' )

# set up colors for error report

ColorSetup() {

  if [ -z "$TERM" ] || [ ! -t 2 ]
  then
    RED=""
    BLUE=""
    BOLD=""
    FLIP=""
    INIT=""
  elif command -v tput >/dev/null
  then
    RED="$(tput setaf 1)"
    BLUE="$(tput setaf 4)"
    BOLD="$(tput bold)"
    FLIP="$(tput rev)"
    INIT="$(tput sgr0)"
  else
    # assume ANSI
    escape="$(printf '\033')"
    RED="${escape}[31m"
    BLUE="${escape}[34m"
    BOLD="${escape}[1m"
    FLIP="${escape}[7m"
    INIT="${escape}[0m"
  fi
  LOUD="${INIT}${RED}${BOLD}"
  INVT="${LOUD}${FLIP}"
  # clear color on terminal if "export EDIRECT_TRACE=true" has been used
  echo "${INIT}" > /dev/null
}

ColorSetup

# highlighted error and warning functions

DisplayError() {

  if [ $# -gt 0 ]
  then
    msg="$1"
    echo "${INVT} ERROR: ${LOUD} ${msg}${INIT}" >&2
  fi
}

DisplayWarning() {

  if [ $# -gt 0 ]
  then
    msg="$1"
    echo "${INVT} WARNING: ${LOUD} ${msg}${INIT}" >&2
  fi
}

DisplayNote() {

  if [ $# -gt 0 ]
  then
    msg="$1"
    echo "${INVT} NOTE: ${LOUD} ${msg}${INIT}" >&2
  fi
}

# parse XML Config/File object

ParseConfig() {

  mesg=$1
  objc=$2
  shift 2

  if [ -z "$mesg" ]
  then
    return 1
  fi

  while [ $# -gt 0 ]
  do
    var=$1
    fld=$2
    shift 2
    value=$( echo "$mesg" | xtract -pattern Rec -ret "" -element "$fld" )
    if [ -n "$value" ]
    then
      eval "$var=\$value"
    fi
  done

  return 0
}

# parse ENTREZ_DIRECT object

ParseMessage() {

  mesg=$1
  objc=$2
  shift 2

  if [ -z "$mesg" ]
  then
    return 1
  fi

  object=$( echo "$mesg" | tr -d '\n' | sed -n "s|.*<$objc>\\(.*\\)</$objc>.*|\\1|p" )
  if [ -z "$object" ]
  then
    return 2
  fi

  err=$( echo "$object" | sed -n 's|.*<Error>\(.*\)</Error>.*|\1|p' )
  if [ -z "$err" ]
  then
    while [ $# -gt 0 ]
    do
      var=$1
      fld=$2
      shift 2
      value=$( echo "$object" | sed -n "s|.*<$fld>\\(.*\\)</$fld>.*|\\1|p" )
      eval "$var=\$value"
    done
  fi

  return 0
}

# check for ENTREZ_DIRECT object, or list of UIDs, piped from stdin

ParseStdin() {

  if [ \( -e /dev/fd/0 -o ! -d /dev/fd \) -a ! -t 0 ]
  then
    mssg=$( cat )
    ParseMessage "$mssg" ENTREZ_DIRECT \
                  dbase Db web_env WebEnv qry_key QueryKey qury Query \
                  mindate MinDate maxdate MaxDate reldate RelDate \
                  datetype DateType num Count
    if [ "$?" = 2 ]
    then
      # if no ENTREZ_DIRECT message present, support passing raw UIDs via stdin
      rest="$mssg"
    else
      # support for UIDs instantiated within message in lieu of Entrez History
      rest=$( echo "$mssg" |
              xtract -pattern ENTREZ_DIRECT -sep "\n" -element Id |
              grep '.' | sort -n | uniq )
    fi
  fi
}

# process common control flags

CheckForArgumentValue() {

  tag="$1"
  rem="$2"

  if [ "$rem" -lt 2 ]
  then
    DisplayError "Missing ${tag} argument"
    exit 1
  fi
}

# ensure date constraint argument consistency

FixDateConstraints() {

  if [ -z "$datetype" ]
  then
    datetype="PDAT"
  fi

  if [ -z "$reldate" ] || [ "$reldate" -lt 1 ]
  then
    reldate=""
  fi

  # set default value for missing date range endpoint
  if [ -n "$mindate" ] && [ -z "$maxdate" ]
  then
    currentDate=$(date +%Y)
    maxdate="$currentDate"
  fi
  if [ -z "$mindate" ] && [ -n "$maxdate" ]
  then
    mindate="1900"
  fi

  if [ -z "$mindate" ] || [ -z "$maxdate" ]
  then
    mindate=""
    maxdate=""
  fi

  if [ -z "$reldate" ] && [ -z "$mindate" ] && [ -z "$maxdate" ]
  then
    datetype=""
  fi
}

# parse ArchivePaths XML object returned by rchive -local

ParseArchivePaths() {

  mesg=$1
  objc=$2
  shift 2

  if [ -z "$mesg" ]
  then
    return 1
  fi

  object=$( echo "$mesg" | tr -d '\n' | sed -n "s|.*<$objc>\\(.*\\)</$objc>.*|\\1|p" )
  if [ -z "$object" ]
  then
    return 2
  fi

  while [ $# -gt 0 ]
  do
    var=$1
    fld=$2
    shift 2
    value=$( echo "$object" | sed -n "s|.*<$fld>\\(.*\\)</$fld>.*|\\1|p" )
    if [ -n "$value" ]
    then
      if [ -n "$osname" ] && [ "$osname" = "CYGWIN_NT" -a -x /bin/cygpath ]
      then
        value=$( cygpath -w "$value" )
      fi

      # remove trailing slash
      value=${value%/}

      eval "$var=\$value"
    fi
  done

  return 0
}

# set paths to all local archive folders

SetLocalArchiveFolders() {

  dbs="$1"

  localPaths=$( rchive -local "$dbase" )

  if [ -z "$localPaths" ] || [ "$localPaths" = "" ]
  then
    echo "ERROR: Must supply path to local data by setting EDIRECT_LOCAL_ARCHIVE environment variable" >&2
    exit 1
  fi

  ParseArchivePaths "$localPaths" ArchivePaths \
    archiveBase "Archive" \
    sentinelsBase "Sentinels" \
    dataBase "Data" \
    postingsBase "Postings" \
    sourceBase "Source" \
    extrasBase "Extras" \
    invertBase "Invert" \
    collectBase "Collect" \
    mergedBase "Merged" \
    scratchBase "Scratch" \
    currentBase "Current" \
    indexedBase "Indexed" \
    invertedBase "Inverted" \
    temporaryBase "Temporary"
}

GetLocalArchiveFolder() {

  fld="$1"

  res=""

  case "$fld" in
    Archive )
      res="$archiveBase"
      ;;
    Sentinels )
      res="$sentinelsBase"
      ;;
    Data )
      res="$dataBase"
      ;;
    Postings )
      res="$postingsBase"
      ;;
    Source )
      res="$sourceBase"
      ;;
    Extras )
      res="$extrasBase"
      ;;
    Invert )
      res="$invertBase"
      ;;
    Collect )
      res="$collectBase"
      ;;
    Merged )
      res="$mergedBase"
      ;;
    Scratch )
      res="$scratchBase"
      ;;
    Current )
      res="$currentBase"
      ;;
    Indexed )
      res="$indexedBase"
      ;;
    Inverted )
      res="$invertedBase"
      ;;
    Temporary )
      res="$temporaryBase"
      ;;
    * )
      ;;
  esac

  echo "$res"
}

# helper for constructing argument arrays

AddIfNotEmpty() {

  if [ -n "$2" ]
  then
    ky=$1
    vl=$2
    shift 2
    "$@" "$ky" "$vl"
  else
    shift 2
    "$@"
  fi
}

# passes all possible data source arguments to ecollect

CallEcollect() {

  # execute query
  res=$( "$@" )

  # use printf percent-s instead of echo to prevent unwanted evaluation of backslash
  printf "%s\n" "$res" | sed -e '${/^$/d;}'
}

GetFromEUtils() {

  set ecollect

  AddIfNotEmpty -db "$dbase" \
  AddIfNotEmpty -web "$web_env" \
  AddIfNotEmpty -key "$qry_key" \
  AddIfNotEmpty -query "$qury" \
  AddIfNotEmpty -reldate "$reldate" \
  AddIfNotEmpty -mindate "$mindate" \
  AddIfNotEmpty -maxdate "$maxdate" \
  AddIfNotEmpty -datetype "$datetype" \
  AddIfNotEmpty -num "$num" \
  CallEcollect "$@"
}

GetUIDs() {

  if [ -n "$web_env" ] && [ -n "$qry_key" ]
  then
    GetFromEUtils
  elif [ -n "$qury" ] && [ "$dbase" = "pubmed" ]
  then
    GetFromEUtils
  elif [ -n "$ids" ]
  then
    echo "$ids" |
    # faster version of accn-at-a-time without case transformation
    tr -cs a-zA-Z0-9_. '\n' | sed 's/^0*//'

  elif [ -n "$rest" ]
  then
    # raw UIDs or instantiated UIDs extracted from ENTREZ_DIRECT message
    echo "$rest" |
    tr -cs a-zA-Z0-9_. '\n' | sed 's/^0*//'

  elif [ -n "$input" ]
  then
    # input file of raw UIDs
    cat "$input" |
    tr -cs a-zA-Z0-9_. '\n' | sed 's/^0*//'

  else
    DisplayError "Missing argument describing data source"
    exit 1
  fi |

  if [ "$dbase" = "pmc" ]
  then
    # remove any PMC prefix
    sed -e 's/^PMC//g' -e 's/^pmc//g' | sed 's/^0*//'
  else
    grep '.'
  fi |

  # sort and unique final UID results
  sort -n | uniq
}
