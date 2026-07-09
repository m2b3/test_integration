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
# File Name:  rcommon.sh
#
# Author:  Jonathan Kans, Aaron Ucko
#
# Version Creation Date:   07/12/2025
#
# ==========================================================================

# environment variable turns on shell tracing

if [ -n "${EDIRECT_TRACE}" ] && [ "${EDIRECT_TRACE}" = true ]
then
  set -x
fi

# initialize common flags

osname=$( uname -s | sed -e 's/_NT-.*$/_NT/; s/^MINGW[0-9]*/CYGWIN/' )

year="$(date +%Y)"

dbase=""
project=""

folder=""

custom=""

step=""

delete=""
fields=""
index=""
name=""

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

localPaths=""

darkMode=false

isLink=false

edirectBase=""
externBase=""

helper=""

result=""

# control flags set by command-line arguments

useFtp=true
useHttps=false
noAspera=false
transportProtocol=""

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

DisplayLog() {

  if [ $# -gt 0 ]
  then
    msg="$1"
    echo "${INVT} LOG: ${LOUD} ${msg}${INIT}" >&2
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

# read command line arguments, set dbase and project variables

while [ $# -gt 0 ]
do
  tag="$1"
  rem="$#"
  case "$tag" in

    -db | -dbase )
      CheckForArgumentValue "$tag" "$rem"
      shift
      dbase="$1"
      shift
      ;;
    -project )
      CheckForArgumentValue "$tag" "$rem"
      shift
      project="$1"
      shift
      ;;

    -helper )
      CheckForArgumentValue "$tag" "$rem"
      shift
      helper="$1"
      shift
      ;;

    -delete )
      CheckForArgumentValue "$tag" "$rem"
      shift
      delete="$1"
      shift
      ;;

    -fields )
      CheckForArgumentValue "$tag" "$rem"
      shift
      fields="$1"
      shift
      ;;

    -index )
      CheckForArgumentValue "$tag" "$rem"
      shift
      index="$1"
      shift
      ;;

    -name )
      CheckForArgumentValue "$tag" "$rem"
      shift
      name="$1"
      shift
      ;;

    -link )
      isLink=true
      shift
      ;;

    -ftp )
      useFtp=true
      useHttps=false
      noAspera=true
      transportProtocol="$tag"
      export EDIRECT_NO_ASPERA=true
      shift
      ;;
    -http | -https )
      useFtp=false
      useHttps=true
      transportProtocol="$tag"
      shift
      ;;

    -dark )
      darkMode=true
      shift
      ;;
    -log )
      logMode=true
      shift
      ;;

    -custom )
      # available to user-supplied code in sourced helper script
      CheckForArgumentValue "$tag" "$rem"
      shift
      custom="$1"
      shift
      ;;

    -step )
      # available to helper for possible special behavior
      CheckForArgumentValue "$tag" "$rem"
      shift
      step="$1"
      shift
      ;;

    "" )
      # ignore empty argument, e.g., -ftp or -https not explicitly set
      shift
      ;;
    -* )
      DisplayError "'$1' is not a recognized command"
      exit 1
      ;;
    * )
      DisplayError "'$1' is not a recognized option"
      exit 1
      ;;
  esac
done

if [ -z "$dbase" ] || [ "$dbase" = "" ]
then
  DisplayError "Missing -db argument"
  exit 1
fi

if [ -z "$project" ] || [ "$project" = "" ]
then
  # default to extern folder with same name as database
  project="$dbase"
fi

if [ -z "$helper" ] || [ "$helper" = "" ]
then
  DisplayError "Missing -helper argument"
  exit 1
fi

# set project folder within extern directory

if [ "$project" = "$dbase" ]
then
  # primary database
  folder="${dbase}"
else
  # secondary project
  folder="${dbase}-${project}"
fi

if [ -z "$folder" ] || [ "$folder" = "" ]
then
  DisplayError "Project folder is not set"
  exit 1
fi

# set paths to all local archive folders

localPaths=$( rchive -local "$dbase" )

if [ -z "$localPaths" ] || [ "$localPaths" = "" ]
then
  DisplayError "Must supply path to local data by setting EDIRECT_LOCAL_ARCHIVE environment variable. EXITING"
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

# all but original pubmed and pmc archives need Go compiler installed

needsGoCompiler=false

if [ "$project" != "pubmed" ] && [ "$project" != "pmc" ]
then
  needsGoCompiler=true
elif [ "$project" != "$dbase" ]
then
  needsGoCompiler=true
fi

if [ "$needsGoCompiler" = true ]
then
  hasgo=$( command -v go )
  if [ ! -x "$hasgo" ]
  then
    DisplayError "The Go (golang) compiler must be installed locally in order to process $project data. EXITING"
    exit 1
  fi
fi

# get paths to edirect and extern folders

hasxtract=$( command -v xtract )
if [ -x "$hasxtract" ]
then
  edirectBase=${hasxtract%/*}
  externBase="${edirectBase}/extern"
fi

if [ -z "$edirectBase" ] || [ "$edirectBase" = "" ] || [ ! -d "$edirectBase" ]
then
  DisplayError "Unable to find database-specific archive scripts."
  exit 1
fi

if [ -z "$externBase" ] || [ "$externBase" = "" ] || [ ! -d "$externBase" ]
then
  DisplayError "Unable to find external archive scripts."
  exit 1
fi

# amount of computer's physical memory, roughly divided by number of concurrent tasks,
# allows optimal garbage collection frequency without disk caching or memory overflow
physicalMemory=$( xtract -stats 2>&1 | grep Mmry | tr ' ' '\n' | grep [0-9*] )

# calculate 3/4 of the computer's memory to set reasonable garbage collection value for population scripts
threeFourthMemory=$(( physicalMemory * 3 / 4 ))

# common incremental indexing function for primary database (project = dbase), with optional transformation file

IncrementalIndex() {

  idxtxt="$1"
  tform=""

  if [ "$#" -gt 1 ]
  then
    tform="$2"
  fi

  temp=$(mktemp /tmp/INDEX_TEMP.XXXXXXXXX)

  # generate file with xtract indexing arguments, split onto separate lines, skipping past xtract command itself
  echo "${idxtxt}" | xargs -n1 echo | grep -v '\-stops' | grep -v '\-stems' | tail -n +2 > $temp

  # calculate 1/2 of the computer's memory to allow optimal garbage collection without disk caching or memory overflow
  halfMemory=$(( physicalMemory / 2 ))

  # primary projects now use combined incremental indexing/inversion
  if [ -n "$tform" ]
  then
    env GOMEMORYLIMIT="${halfMemory}GiB" rchive -db "$dbase" -name "$name" -e2IndexInvert -idxargs "$temp" -transform "${tform}" -e2index
  else
    env GOMEMORYLIMIT="${halfMemory}GiB" rchive -db "$dbase" -name "$name" -e2IndexInvert -idxargs "$temp" -e2index
  fi

  rm "$temp"
}

# check to see if helper file exists (should have been passed full path)

if [ ! -f "${helper}" ]
then
  echo "ERROR: Unable to find '${helper}' helper file" >&2
  exit 1
fi

# import and execute helper code - dot command is equivalent of "source"

. "${helper}"

# if helper code set result variable, echo it as return value

if [ -n "$result" ]
then
  echo "$result"
fi
