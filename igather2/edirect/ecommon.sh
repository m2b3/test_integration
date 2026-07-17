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
# File Name:  ecommon.sh
#
# Author:  Jonathan Kans, Aaron Ucko
#
# Version Creation Date:   04/17/2020
#
# ==========================================================================

# echo "ecommon.sh $@" >&2

# environment variable turns on shell tracing

if [ -n "${EDIRECT_TRACE}" ] && [ "${EDIRECT_TRACE}" = true ]
then
  set -x
fi

version="25.1"

# initialize common flags

raw=false
dev=false
internal=false
external=false
api_key=""
immediate=false
express=false

reldate=""
mindate=""
maxdate=""
datetype=""

# undocumented -quick argument bypasses logic that works around PubMed/PMC SOLR server limits
quick=false
quickx=false

email=""
emailr=""
emailx=""

tool="edirect"
toolr=""
toolx=""

debug=false
debugx=false

log=false
logx=false

# timer now on by default
timer=true
timerx=false

label=""
labels=""
labelx=""

reldatex=""
mindatex=""
maxdatex=""
datetypex=""

verbose=false
tranquil=false

basx=""
base="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

argsConsumed=0

seconds_start=$(date "+%s")

# initialize database and identifier command-line variables

db=""

ids=""
input=""

needHistory=false

# initialize EDirect message fields

mssg=""
err=""
dbase=""
web_env=""
qry_key=""
num=0
empty=false
stp=0

rest=""
qury=""

# environment variable disables retry attempts

showErrors=true
if [ -n "${EDIRECT_NO_ERRORS}" ] && [ "${EDIRECT_NO_ERRORS}" = true ]
then
  showErrors=false
fi

retryAllowed=true
if [ -n "${EDIRECT_NO_RETRY}" ] && [ "${EDIRECT_NO_RETRY}" = true ]
then
  retryAllowed=false
fi

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

# parse ENTREZ_DIRECT, eSearchResult, eLinkResult, or ePostResult

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

AdjustEmailAndTool() {

  # hierarchy is -email argument, then Email XML field, then calculated email

  if [ -n "$emailr" ]
  then
    emailx="$emailr"
  fi
  if [ -n "$emailx" ]
  then
    email="$emailx"
  fi

  if [ -n "$toolr" ]
  then
    toolx="$toolr"
  fi
  if [ -n "$toolx" ]
  then
    tool="$toolx"
  fi
}

# check for ENTREZ_DIRECT object, or list of UIDs, piped from stdin

ParseStdin() {

  if [ \( -e /dev/fd/0 -o ! -d /dev/fd \) -a ! -t 0 ]
  then
    mssg=$( cat )
    ParseMessage "$mssg" ENTREZ_DIRECT \
                  dbase Db web_env WebEnv qry_key QueryKey qury Query \
                  mindatex MinDate maxdatex MaxDate reldatex RelDate \
                  datetypex DateType num Count \
                  stp Step toolx Tool emailx Email labelx Labels \
                  quickx Quick debugx Debug logx Log timerx Elapsed
    if [ "$?" = 2 ]
    then
      # if no ENTREZ_DIRECT message present, support passing raw UIDs via stdin
      rest="$mssg"
    else
      # support for UIDs instantiated within message in lieu of Entrez History
      rest=$( echo "$mssg" |
              xtract -pattern ENTREZ_DIRECT -sep "\n" -element Id |
              grep '.' | sort -n | uniq )
      if [ -z "$stp" ]
      then
        stp=1
      fi
      # hierarchy is -email argument, then Email XML field, then calculated email
      AdjustEmailAndTool
      if [ "$quickx" = "Y" ]
      then
        quick=true
      fi
      if [ "$debugx" = "Y" ]
      then
        debug=true
      fi
      if [ "$logx" = "Y" ]
      then
        log=true
      fi
      if [ -n "$timerx" ]
      then
        timer=true
      fi
      if [ -n "$labelx" ]
      then
        labels="$labelx"
      fi
      cnt=$( echo "$mssg" | xtract -pattern ENTREZ_DIRECT -element Count )
      if [ -n "$cnt" ] && [ "$cnt" = "0" ]
      then
        empty=true
      fi
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

ParseCommonArgs() {

  argsConsumed=0
  while [ $# -gt 0 ]
  do
    tag="$1"
    rem="$#"
    case "$tag" in
      -dev )
        dev=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -raw )
        raw=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -internal | -int )
        internal=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -external | -ext )
        external=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -immediate )
        immediate=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -express )
        express=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -base )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        basx="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -input )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        input="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -web )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        web_env="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -step )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        stp="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -label )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        stp="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -email )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        emailr="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -tool )
        argsConsumed=$((argsConsumed + 1))
        CheckForArgumentValue "$tag" "$rem"
        shift
        toolr="$1"
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -quick )
        argsConsumed=$((argsConsumed + 1))
        shift
        if [ $# -gt 0 ]
        then
          if [ "$1" = "true" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            quick=true
          elif [ "$1" = "false" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            quick=false
          else
            quick=true
          fi
        else
          quick=true
        fi
        ;;
      -debug )
        argsConsumed=$((argsConsumed + 1))
        shift
        if [ $# -gt 0 ]
        then
          if [ "$1" = "true" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            debug=true
          elif [ "$1" = "false" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            debug=false
          else
            debug=true
          fi
        else
          debug=true
        fi
        ;;
       -verbose )
        argsConsumed=$((argsConsumed + 1))
        shift
        if [ $# -gt 0 ]
        then
          if [ "$1" = "true" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            verbose=true
          elif [ "$1" = "false" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            verbose=false
          else
            verbose=true
          fi
        else
          verbose=true
        fi
        ;;
      -tranquil )
        tranquil=true
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      -clear )
        quick=false
        quickx=false
        debug=false
        debugx=false
        verbose=false
        tranquil=false
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
     -log )
        argsConsumed=$((argsConsumed + 1))
        shift
        if [ $# -gt 0 ]
        then
          if [ "$1" = "true" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            log=true
          elif [ "$1" = "false" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            log=false
          else
            log=true
          fi
        else
          log=true
        fi
        ;;
      -timer )
        argsConsumed=$((argsConsumed + 1))
        shift
        if [ $# -gt 0 ]
        then
          if [ "$1" = "true" ] || [ "$1" = "on" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            timer=true
          elif [ "$1" = "false" ] || [ "$1" = "off" ]
          then
            argsConsumed=$((argsConsumed + 1))
            shift
            timer=false
          else
            timer=true
          fi
        else
          timer=true
        fi
        ;;
      -version )
        echo "$version"
        exit 0
        ;;
      -newmode | -oldmode )
        argsConsumed=$((argsConsumed + 1))
        shift
        ;;
      * )
        # allows while loop to check for multiple flags
        break
        ;;
    esac
  done
}

FinishSetup() {

  # adjust base URL address

  case "${EXTERNAL_EDIRECT}" in
    "" | [FfNn]* | 0 | [Oo][Ff][Ff] )
      ;;
    * )
      external=true
      ;;
  esac

  if [ "$external" = true ]
  then
    internal=false
  fi

  if [ -n "$basx" ]
  then
    base="$basx"
  elif [ "$dev" = true ]
  then
    base="https://dev.ncbi.nlm.nih.gov/entrez/eutils/"
  elif [ "$internal" = true ]
  then
    base="https://eutils-internal.ncbi.nlm.nih.gov/entrez/eutils/"
  fi

  # read API Key from environment variable

  if [ -n "${NCBI_API_KEY}" ]
  then
    api_key="${NCBI_API_KEY}"
  fi

  # determine contact email address

  os=$( uname -s | sed -e 's/_NT-.*$/_NT/; s/^MINGW[0-9]*/CYGWIN/' )

  if [ -n "${EMAIL}" ]
  then
    email="${EMAIL}"
  else
    # Failing that, try to combine the username from USER or whoami
    # with the contents of /etc/mailname if available or the system's
    # qualified host name.  (Its containing domain may be a better
    # choice in many cases, but anyone contacting abusers can extract
    # it if necessary.)
    lhs=""
    rhs=""
    if [ -n "${USER}" ]
    then
      lhs="${USER}"
    else
      lhs=$( id -un )
    fi
    if [ -s "/etc/mailname" ]
    then
      rhs=$( cat /etc/mailname )
    else
      rhs=$( hostname -f 2>/dev/null || uname -n )
      case "$rhs" in
        *.* ) # already qualified
          ;;
        * )
          output=$( host "$rhs" 2>/dev/null )
          case "$output" in
            *.*' has address '* )
              rhs=${output% has address *}
              ;;
          esac
          ;;
      esac
    fi
    if [ -n "$lhs" ] && [ -n "$rhs" ]
    then
      # convert any spaces in user name to underscores
      lhs=$( echo "$lhs" | sed -e 's/ /_/g' )
      email="${lhs}@${rhs}"
    fi
  fi

  # -email argument overrides calculated email, and Email XML field, if read later

  AdjustEmailAndTool

  # temporarily remove email name starting with "root@", which crashes server
  if [ -n "$email" ]
  then
    case "$email" in
      "root@"* )
        email=""
        ;;
      * )
        ;;
    esac
  fi
}

# prints query command with double quotes around multi-word arguments

PrintQuery() {

  if printf "%q" >/dev/null 2>&1
  then
    fmt="%q"
  else
    fmt="%s"
  fi
  dlm=""
  for elm in "$@"
  do
    raw="$elm"
    num=$( printf "%s" "$elm" | wc -w | tr -cd 0-9 )
    [ "$fmt" = "%s" ] || elm=$( printf "$fmt" "$elm" )
    case "$elm:$num:$fmt" in
      *[!\\][\'\"]:*:%q )
        ;;
      *:1:* )
        elm=$( printf "%s" "$raw" | LC_ALL=C sed -e 's/\([]!-*<>?[\\]\)/\\\1/g' )
        ;;
      *:%q )
        elm="\"$( printf "%s" "$elm" | sed -e 's/\\\([^\\"`$]\)/\1/g' )\""
        ;;
      * )
        elm="\"$( printf "%s" "$raw" | sed -e 's/\([\\"`$]\)/\\\1/g' )\""
        ;;
    esac
    printf "$dlm%s" "$elm"
    dlm=" "
  done >&2
  printf "\n" >&2
}

# three attempts for EUtils requests

ErrorHead() {

  wrn="$1"
  whn="$2"

  printf "${INVT} ${wrn}: ${LOUD} FAILURE ( $whn )${INIT}\n" >&2
  # display original command in blue letters
  printf "${BLUE}" >&2
}

ErrorTail() {

  msg="$1"
  whc="$2"

  printf "${INIT}" >&2
  # display reformatted result in red letters
  lin=$( echo "${msg}" | wc -l)
  if [ -n "$lin" ] && [ "$lin" -gt 25 ]
  then
    hd=$( echo "${msg}" | head -n 10 )
    tl=$( echo "${msg}" | tail -n 10 )
    printf "${RED}${hd}${INIT}\n" >&2
    printf "${RED}...${INIT}\n" >&2
    printf "${RED}${tl}${INIT}\n" >&2
  else
    printf "${RED}${msg}${INIT}\n" >&2
  fi
  if [ "$goOn" = true ] && [ "$retryAllowed" = true ]
  then
    printf "${BLUE}${whc} ATTEMPT" >&2
  else
    printf "${BLUE}QUERY FAILURE" >&2
  fi
  printf "${INIT}\n" >&2
}

RequestWithRetry() {

  retryDelay=0
  tries=3
  goOn=true
  when=$( date )

  # execute query
  res=$( "$@" )

  warn="WARNING"
  whch="SECOND"

  while [ "$goOn" = true ]
  do
    retryDelay=$(( retryDelay + 1 ))
    tries=$(( tries - 1 ))
    if [ "$tries" -lt 1 ]
    then
      goOn=false
      warn="ERROR"
    fi
    case "$res" in
      "" )
        # empty result
        if [ "$showErrors" = true ]
        then
          ErrorHead "$warn" "$when"
          PrintQuery "$@"
          ErrorTail "EMPTY RESULT" "$whch"
        fi
        sleep 1
        when=$( date )
        if [ "$retryAllowed" = true ]
        then
          # retry query
          res=$( "$@" )
        fi
        ;;
      *\<eFetchResult\>* | *\<eSummaryResult\>*  | *\<eSearchResult\>*  | *\<eLinkResult\>* | *\<ePostResult\>* | *\<eInfoResult\>* )
        case "$res" in
          *\<ERROR\>* )
            ref=$( echo "$res" | transmute -format indent -doctype "" )
            if [ "$showErrors" = true ]
            then
              ErrorHead "$warn" "$when"
              PrintQuery "$@"
              if [ "$goOn" = true ]
              then
                # asterisk prints entire selected XML subregion
                ref=$( echo "$res" | xtract -pattern ERROR -element "*" )
              fi
              ErrorTail "$ref" "$whch"
            fi
            sleep 1
            when=$( date )
            if [ "$retryAllowed" = true ]
            then
              # retry query
              res=$( "$@" )
            fi
            ;;
          *\<error\>* )
            ref=$( echo "$res" | transmute -format indent -doctype "" )
            if [ "$showErrors" = true ]
            then
              ErrorHead "$warn" "$when"
              PrintQuery "$@"
              if [ "$goOn" = true ]
              then
                # asterisk prints entire selected XML subregion
                ref=$( echo "$res" | xtract -pattern error -element "*" )
              fi
              ErrorTail "$ref" "$whch"
            fi
            sleep 1
            when=$( date )
            if [ "$retryAllowed" = true ]
            then
              # retry query
              res=$( "$@" )
            fi
            ;;
          *\<ErrorList\>* )
            ref=$( echo "$res" | transmute -format indent -doctype "" )
            # question mark prints names of heterogeneous child objects
            errs=$( echo "$res" | xtract -pattern "ErrorList/*" -element "?" | sort -f | uniq -i )
            if [ -n "$errs" ] && [ "$errs" = "PhraseNotFound" ]
            then
              goOn=false
            else
              if [ "$showErrors" = true ]
              then
                ErrorHead "$warn" "$when"
                PrintQuery "$@"
                if [ "$goOn" = true ]
                then
                  # reconstruct indented ErrorList XML
                  ref=$( echo "$res" | xtract -head "<ErrorList>" -tail "<ErrorList>" \
                         -pattern "ErrorList/*" -pfx "  " -element "*" )
                fi
                ErrorTail "$ref" "$whch"
              fi
              sleep 1
              when=$( date )
              if [ "$retryAllowed" = true ]
              then
                # retry query
                res=$( "$@" )
              fi
            fi
            ;;
          *\"error\":* )
            ref=$( echo "$res" | transmute -format indent -doctype "" )
            if [ "$showErrors" = true ]
            then
              ErrorHead "$warn" "$when"
              PrintQuery "$@"
              ErrorTail "$ref" "$whch"
            fi
            sleep 1
            when=$( date )
            if [ "$retryAllowed" = true ]
            then
              # retry query
              res=$( "$@" )
            fi
            ;;
          *"<DocumentSummarySet status=\"OK\"><!--"* )
            # 'DocSum Backend failed' message embedded in comment
            if [ "$showErrors" = true ]
            then
              ErrorHead "$warn" "$when"
              PrintQuery "$@"
              ErrorTail "$res" "$whch"
            fi
            sleep 1
            when=$( date )
            if [ "$retryAllowed" = true ]
            then
              # retry query
              res=$( "$@" )
            fi
            ;;
          *\<WarningList\>* )
            case "$res" in
              *"<OutputMessage>No items found"* )
                if [ "$tranquil" = true ]
                then
                  # -tranquil flag conditionally ignores no items found message
                  goOn=false
                else
                  ref=$( echo "$res" | transmute -format indent -doctype "" )
                  # errs=$( echo "$res" | xtract -pattern "WarningList/*" -element "?" )
                  if [ "$showErrors" = true ]
                  then
                    ErrorHead "$warn" "$when"
                    PrintQuery "$@"
                    if [ "$goOn" = true ]
                    then
                      # reconstruct indented ErrorList XML
                      ref=$( echo "$res" | xtract -head "<WarningList>" -tail "<WarningList>" \
                             -pattern "WarningList/*" -pfx "  " -element "*" )
                    fi
                    ErrorTail "$ref" "$whch"
                  fi
                  sleep 1
                  when=$( date )
                  if [ "$retryAllowed" = true ]
                  then
                    # retry query
                    res=$( "$@" )
                  fi
                fi
                ;;
              *"<OutputMessage>Wildcard search for"*"used only the first 600 variations"* )
                if [ "$tranquil" = true ]
                then
                  # -tranquil flag conditionally ignores wildcard search root too short message
                  goOn=false
                else
                  goOn=false
                  if [ "$showErrors" = true ]
                  then
                    ErrorHead "$warn" "$when"
                    errs=$( echo "$res" | xtract -pattern "WarningList/*" -element OutputMessage )
                    ErrorTail "$errs" "$whch"
                  fi
                  sleep 1
                fi
                ;;
              * )
                ref=$( echo "$res" | transmute -format indent -doctype "" )
                # errs=$( echo "$res" | xtract -pattern "WarningList/*" -element "?" )
                if [ "$showErrors" = true ]
                then
                  ErrorHead "$warn" "$when"
                  PrintQuery "$@"
                  if [ "$goOn" = true ]
                  then
                    # reconstruct indented ErrorList XML
                    ref=$( echo "$res" | xtract -head "<WarningList>" -tail "<WarningList>" \
                           -pattern "WarningList/*" -pfx "  " -element "*" )
                  fi
                  ErrorTail "$ref" "$whch"
                fi
                sleep 1
                when=$( date )
                if [ "$retryAllowed" = true ]
                then
                  # retry query
                  res=$( "$@" )
                fi
                ;;
            esac
            ;;
          * )
            # success - no error message detected
            goOn=false
            ;;
        esac
        ;;
      *"<DocumentSummarySet status=\"OK\"><!--"* )
        # docsum with comment not surrounded by wrapper
        if [ "$showErrors" = true ]
        then
          ErrorHead "$warn" "$when"
          PrintQuery "$@"
          ErrorTail "$res" "$whch"
        fi
        sleep 1
        when=$( date )
        if [ "$retryAllowed" = true ]
        then
          # retry query
          res=$( "$@" )
        fi
        ;;
      * )
        # success for non-structured or non-EUtils-XML result
        goOn=false
        ;;
    esac
    whch="LAST"
    if [ "$retryAllowed" = false ]
    then
      goOn=false
    fi
  done

  # use printf percent-s instead of echo to prevent unwanted evaluation of backslash
  printf "%s\n" "$res" | sed -e '${/^$/d;}'
}

# optionally prints command, then executes it with retry on failure

RunWithLogging() {

  if [ "$debug" = true ]
  then
    PrintQuery "$@"
  fi

  RequestWithRetry "$@"
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

# helpers for constructing argument arrays

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

FlagIfNotEmpty() {

  if [ -n "$2" ] && [ "$2" = true ]
  then
    ky=$1
    shift 2
    "$@" "$ky"
  else
    shift 2
    "$@"
  fi
}

# helper function adds common tracking arguments

RunWithCommonArgs() {

  AddIfNotEmpty -api_key "$api_key" \
  AddIfNotEmpty -tool "$tool" \
  AddIfNotEmpty -edirect "$version" \
  AddIfNotEmpty -edirect_os "$os" \
  AddIfNotEmpty -email "$email" \
  RunWithLogging "$@"
}

# break Entrez history server requests into chunks

GenerateHistoryChunks() {

  chnk="$1"
  minn="$2"
  maxx="$3"

  if [ "$minn" -gt 0 ]
  then
    minn=$(( minn - 1 ))
  fi
  if [ "$maxx" -eq 0 ]
  then
    maxx="$num"
  fi

  fr="$minn"

  while [ "$fr" -lt "$maxx" ]
  do
    to=$(( fr + chnk ))
    if [ "$to" -gt "$maxx" ]
    then
      chnk=$(( maxx - fr ))
    fi
    echo "$fr" "$chnk"
    fr=$(( fr + chnk ))
  done
}

# passes all possible data source arguments to ecollect

CallEcollect() {

  # execute query
  res=$( "$@" )

  # use printf percent-s instead of echo to prevent unwanted evaluation of backslash
  printf "%s\n" "$res" | sed -e '${/^$/d;}'
}

GetFromEcollect() {

#  ecollect -base "$base" -db "$dbase" -web "$web_env" -key "$qry_key" \
#    -query "$qury" -id "$ids" -rest "$rest" -input "$input" -num "$num"

  set ecollect

  AddIfNotEmpty -base "$base" \
  AddIfNotEmpty -db "$dbase" \
  AddIfNotEmpty -web "$web_env" \
  AddIfNotEmpty -key "$qry_key" \
  AddIfNotEmpty -query "$qury" \
  AddIfNotEmpty -reldate "$reldate" \
  AddIfNotEmpty -mindate "$mindate" \
  AddIfNotEmpty -maxdate "$maxdate" \
  AddIfNotEmpty -datetype "$datetype" \
  AddIfNotEmpty -id "$ids" \
  AddIfNotEmpty -rest "$rest" \
  AddIfNotEmpty -input "$input" \
  AddIfNotEmpty -num "$num" \
  CallEcollect "$@"
}

GetUIDs() {

  if [ -n "$web_env" ] && [ -n "$qry_key" ]
  then
    GetFromEcollect

  elif [ -n "$qury" ]
  then
    GetFromEcollect

  elif [ -n "$ids" ] && [ "$dbase" = "pmc" ]
  then
    # LookupSpecialAccessions instantiates converted accessions into $ids variable,
    # so -id argument, if populated, must be used before $rest and $input
    echo "$ids" |
    # faster version of accn-at-a-time without case transformation
    sed -e 's/^PMC//g' | sed -e 's/^pmc//g' | tr -cs a-zA-Z0-9_. '\n' | fmt -w 1 | sort -n | uniq

  elif [ -n "$ids" ]
  then
    # LookupSpecialAccessions instantiates converted accessions into $ids variable,
    # so -id argument, if populated, must be used before $rest and $input
    echo "$ids" |
    # faster version of accn-at-a-time without case transformation
    tr -cs a-zA-Z0-9_. '\n'

  elif [ -n "$rest" ]
  then
    # raw UIDs or instantiated UIDs extracted from ENTREZ_DIRECT message
    echo "$rest" |
    tr -cs a-zA-Z0-9_. '\n'

  elif [ -n "$input" ]
  then
    # input file of raw UIDs
    cat "$input" |
    tr -cs a-zA-Z0-9_. '\n'

  else
    DisplayError "Missing argument describing data source"
    exit 1
  fi |

  # sort and unique final UID results
  sort -n | uniq
}

# special case accession to UID lookup functions

ExtractNucUids() {

  # argument value: 1 = PACC, 2 = ACCN, 3 = integer and accession
  kind="$1"

  while read uid
  do
    case "$uid" in
      *00000000 )
        notInteger=$( echo "$uid" | sed -e 's/[0-9.]//g' )
        if [ -n "$notInteger" ]
        then
          if [ "$kind" -eq 1 ]
          then
            echo "$uid"
          fi
        else
          if [ "$kind" -eq 3 ]
          then
            echo "$uid"
          fi
        fi
        ;;
      *0000000 )
        notInteger=$( echo "$uid" | sed -e 's/[0-9.]//g' )
        if [ -n "$notInteger" ]
        then
          if [ "$kind" -eq 2 ]
          then
            echo "$uid"
          fi
        else
          if [ "$kind" -eq 3 ]
          then
            echo "$uid"
          fi
        fi
        ;;
      * )
        if [ "$kind" -eq 3 ]
        then
          echo "$uid"
        fi
        ;;
    esac
  done
}

ExtractPDB() {

  GetUIDs |
  while read uid
  do
    case "$uid" in
      [0-9][0-9][0-9][0-9] )
        # Four-digit UID
        # peel off first to avoid mistaking for a chainless PDB ID
        ;;
      [0-9][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z] | \
      [0-9][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z]_[A-Za-z]* )
        # PDB ID
        # properly case-sensitive only when untagged
        echo "$uid"
        ;;
    esac
  done
}

ExtractNonPDB() {

  GetUIDs |
  while read uid
  do
    case "$uid" in
      [0-9][0-9][0-9][0-9] )
        # Four-digit UID
        # peel off first to avoid mistaking for a chainless PDB ID
        echo "$uid"
        ;;
      [0-9][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z] | \
      [0-9][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z]_[A-Za-z]* )
        # PDB ID, skip
        ;;
      *[A-Za-z]* )
        # accessions are already handled
        echo "$uid"
        ;;
      * )
        echo "$uid"
        ;;
    esac
  done
}

PrepareAccnQuery() {

  while read uid
  do
    echo "$uid+[$1]"
  done |
  join-into-groups-of "$2" |
  sed -e 's/,/ OR /g' |
  tr '+' ' '
}

RunAccnSearch() {

  while read qry
  do
    nquire -url "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" \
      -db "$dbase" -term "$qry" -retmax "$1" < /dev/null |
    xtract -pattern eSearchResult -sep "\n" -element IdList/Id
  done
}

PreparePDBQuery() {

  while read uid
  do
    echo "$uid"
  done |
  join-into-groups-of 10000 |
  sed -e 's/,/ OR /g'
}

PrepareSnpQuery() {

  while read uid
  do
    case "$uid" in
      rs* )
        echo "$uid+[RS]"
        ;;
      ss* )
        echo "$uid+[SS]"
        ;;
    esac
  done |
  join-into-groups-of 10000 |
  sed -e 's/,/ OR /g' |
  tr '+' ' '
}

LookupSpecialAccessions() {

  if [ -z "$web_env" ] && [ -z "$qry_key" ] && [ -z "$qury" ]
  then
    fld=""
    case "$dbase" in
      assembly | annotinfo )
        fld="ASAC"
        ;;
      biosample | biosystems | cdd | dbvar | ipg | medgen | proteinclusters | seqannot | sra )
        fld="ACCN"
        ;;
      bioproject | genome )
        fld="PRJA"
        ;;
      books )
        fld="AID"
        ;;
      clinvar )
        fld="VACC"
        ;;
      gds )
        fld="ALL"
        ;;
      geoprofiles )
        fld="NAME"
        ;;
      gtr )
        fld="GTRACC"
        ;;
      mesh )
        fld="MHUI"
        ;;
      pcsubstance )
        fld="SRID"
        ;;
      nuc* )
        nucUidList=$( GetUIDs )
        anyNonInteger=$( echo "$nucUidList" | sed -e 's/[0-9.]//g' )
        if [ -n "$anyNonInteger" ]
        then
          pacc=$( echo "$nucUidList" | ExtractNucUids "1" )
          accn=$( echo "$nucUidList" | ExtractNucUids "2" )
          lcl=$( echo "$nucUidList" | ExtractNucUids "3" )
          pacres=""
          accres=""
          if [ -n "$pacc" ]
          then
            pacres=$( echo "$pacc" |
                      PrepareAccnQuery "PACC" "100" |
                      RunAccnSearch "1000" )
          fi
          if [ -n "$accn" ]
          then
            accres=$( echo "$accn" |
                      PrepareAccnQuery "ACCN" "100" |
                      RunAccnSearch "1000" )
          fi
          if [ -n "$pacres" ] || [ -n "$accres" ]
          then
            ids=$( echo "$pacres $accres $lcl" | fmt -w 1 | sort -n | uniq )
          fi
        else
          ids="$nucUidList"
        fi
        ;;
      protein )
        acc=$( ExtractPDB )
        lcl=$( ExtractNonPDB )
        if [ -n "$acc" ]
        then
          query=$( echo "$acc" | PreparePDBQuery "$fld" )
          rem=$( esearch -db "$dbase" -query "$query" | efetch -format uid )
          ids=$( echo "$rem $lcl" | fmt -w 1 | sort -n | uniq )
        fi
        ;;
      snp )
        snpUidList=$( GetUIDs )
        anyNonInteger=$( echo "$snpUidList" | sed -e 's/[0-9.]//g' )
        if [ -n "$anyNonInteger" ]
        then
          acc=$( echo "$snpUidList" | grep -v '^[0-9]*$' )
          lcl=$( echo "$snpUidList" | grep '^[0-9]*$' )
          if [ -n "$acc" ]
          then
            query=$( echo "$acc" | PrepareSnpQuery "$fld" )
            rem=$( esearch -db "$dbase" -query "$query" | efetch -format uid )
            ids=$( echo "$rem $lcl" | fmt -w 1 | sort -n | uniq )
          else
            ids=$( echo "$lcl" | fmt -w 1 | sort -n | uniq )
          fi
        else
          ids=$( echo "$snpUidList" | fmt -w 1 | sort -n | uniq )
        fi
        ;;
      taxonomy )
        taxUidList=$( GetUIDs )
        anyNonInteger=$( echo "$taxUidList" | sed -e 's/[0-9.]//g' )
        if [ -n "$anyNonInteger" ]
        then
          DisplayError "Taxonomy database does not index sequence accession numbers"
          exit 1
        else
          ids=$( echo "$taxUidList" | fmt -w 1 | sort -n | uniq )
        fi
        ;;
    esac
    if [ -n "$fld" ]
    then
      otherUidList=$( GetUIDs )
      anyNonInteger=$( echo "$otherUidList" | sed -e 's/[0-9.]//g' )
      if [ -n "$anyNonInteger" ]
      then
        acc=$( echo "$otherUidList" | grep -v '^[0-9]*$' )
        lcl=$( echo "$otherUidList" | grep '^[0-9]*$' )
        if [ -n "$acc" ]
        then
          newids=$( echo "$acc" |
                    PrepareAccnQuery "$fld" "1000" |
                    RunAccnSearch "10000" )
          if [ -n "$newids" ]
          then
            ids=$( echo "$newids $lcl" | fmt -w 1 | sort -n | uniq )
          else
            ids=$( echo "$lcl" | fmt -w 1 | sort -n | uniq )
          fi
        else
          ids=$( echo "$lcl" | fmt -w 1 | sort -n | uniq )
        fi
      else
        ids=$( echo "$otherUidList" | fmt -w 1 | sort -n | uniq )
      fi
    fi
  fi
}

# write minimal ENTREZ_DIRECT message for intermediate processing

WriteEDirectStep() {

  dbsx="$1"
  webx="$2"
  keyx="$3"
  errx="$4"

  echo "<ENTREZ_DIRECT>"

  if [ -n "$dbsx" ]
  then
    echo "  <Db>${dbsx}</Db>"
  fi
  if [ -n "$webx" ]
  then
    echo "  <WebEnv>${webx}</WebEnv>"
  fi
  if [ -n "$keyx" ]
  then
    echo "  <QueryKey>${keyx}</QueryKey>"
  fi
  if [ -n "$errx" ]
  then
    echo "  <Error>${errx}</Error>"
  fi

  echo "</ENTREZ_DIRECT>"
}

# write ENTREZ_DIRECT data structure

WriteEDirect() {

  dbsx="$1"
  webx="$2"
  keyx="$3"
  numx="$4"
  stpx="$5"
  errx="$6"

  seconds_end=$(date "+%s")
  seconds_elapsed=$((seconds_end - seconds_start))

  echo "<ENTREZ_DIRECT>"

  if [ -n "$dbsx" ]
  then
    echo "  <Db>${dbsx}</Db>"
  fi
  if [ -n "$webx" ]
  then
    echo "  <WebEnv>${webx}</WebEnv>"
  fi
  if [ -n "$keyx" ]
  then
    echo "  <QueryKey>${keyx}</QueryKey>"
  fi
  if [ -n "$numx" ]
  then
    echo "  <Count>${numx}</Count>"
  fi

  if [ -n "$stpx" ]
  then
    # increment step value
    stpx=$(( stpx + 1 ))
    echo "  <Step>${stpx}</Step>"
  fi
  if [ -n "$errx" ]
  then
    echo "  <Error>${errx}</Error>"
  fi
  if [ -n "$toolx" ]
  then
    echo "  <Tool>${toolx}</Tool>"
  fi
  if [ -n "$emailx" ]
  then
    echo "  <Email>${emailx}</Email>"
  fi

  if [ -n "$label" ] && [ -n "$keyx" ]
  then
    labels="<Label><Key>${label}</Key><Val>${keyx}</Val></Label>${labels}"
  fi
  if [ -n "$labels" ]
  then
    echo "  <Labels>"
    echo "$labels" |
    # xtract -pattern Label -element "*"
    xtract -pattern Label -tab "\n" \
      -fwd "    <Label>\n" -awd "\n    </Label>" \
      -pfx "      <Key>" -sfx "</Key>" -element Key \
      -pfx "      <Val>" -sfx "</Val>" -element Val
    echo "  </Labels>"
  fi

  if [ "$quick" = true ] || [ "$quickx" = "Y" ]
  then
    echo "  <Quick>Y</Quick>"
  fi
  if [ "$debug" = true ] || [ "$debugx" = "Y" ]
  then
    echo "  <Debug>Y</Debug>"
  fi
  if [ "$log" = true ] || [ "$logx" = "Y" ]
  then
    echo "  <Log>Y</Log>"
  fi

  if [ "$timer" = true ] && [ -n "$seconds_elapsed" ]
  then
    echo "  <Elapsed>${seconds_elapsed}</Elapsed>"
  fi

  echo "</ENTREZ_DIRECT>"
}

# check for new EDirect version on ftp site

NewerEntrezDirectVersion () {

  newer=$(
    nquire -lst ftp://ftp.ncbi.nlm.nih.gov/entrez/entrezdirect/versions |
    cut -d '.' -f 1,2 | sort -t '.' -k 1,1n -k 2,2n | tail -n 1
  )

  newerWhole=$( echo "$newer" | cut -d '.' -f 1 )
  newerFract=$( echo "$newer" | cut -d '.' -f 2 )

  currentWhole=$( echo "$version" | cut -d '.' -f 1 )
  currentFract=$( echo "$version" | cut -d '.' -f 2 )

  if [ "$newerWhole" -gt "$currentWhole" ]
  then
    echo "$newer"
  elif [ "$newerWhole" -eq "$currentWhole" ] && [ "$newerFract" -gt "$currentFract" ]
  then
    echo "$newer"
  else
    echo ""
  fi
}
