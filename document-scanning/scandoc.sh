#!/usr/bin/env bash

# Constants
DIR="$(xdg-user-dir DOCUMENTS)/scans"
FILE_FORMAT="tiff"
BATCH_FORMAT="p%02d"
SCANNER="airscan:e0:Brother MFC-L2800DW (USB)"

# Initialize flags
DOUBLE=false
BACK=false
FRONT=false

FILE_PREFIX="$(date +%Y%m%d-%H%M)"

# Parse command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --double) DOUBLE=true ;;
        --back) BACK=true ;;
        --front) FRONT=true ;;
        *) 
            # Capture the unnamed parameter as the prefix
            FILE_PREFIX="$1"
            ;;
    esac
    shift
done

# Check for the exclusivity requirement
if [ "$DOUBLE" = true ] && [ "$BACK" = false ] && [ "$FRONT" = false ]; then
    echo "Error: --back or --front must be specified when --double is used."
    exit 1
fi

# Check for exclusivity between --back and --front
if [ "$BACK" = true ] && [ "$FRONT" = true ]; then
    echo "Error: --back and --front cannot be used together."
    exit 1
fi

if [ ! -d "${DIR}" ]; then
    mkdir "${DIR}"
fi

BATCH_START=1
if [ "$DOUBLE" = true ] && [ "$BACK" = true ]; then
    BATCH_START=2
fi

BATCH_INCREMENT=1
if [ "$DOUBLE" = true ]; then
    BATCH_INCREMENT=2
fi


OUTPUT="${DIR}/${FILE_PREFIX}-${BATCH_FORMAT}.${FILE_FORMAT}"

#SCANNER="artec_eplus48u:libusb:003:002"
if [ -f "${DIR}/${FILE_PREFIX}-0${BATCH_START}.${FILE_FORMAT}" ]; then
    echo "File ${DIR}/${FILE_PREFIX}-0${BATCH_START}.tiff already exists, quitting"
    exit 1
fi

scanimage --source ADF --batch=${OUTPUT} --batch-start="${BATCH_START}" --batch-increment="${BATCH_INCREMENT}" --device-name="${SCANNER}" --format="${FILE_FORMAT}" --resolution=300 --mode=Gray -x 210 -y 297

# Perform checks regarding double pages before deciding whether to stich pages together
if [ "${DOUBLE}" = true ] && [ "${FRONT}" = true ]; then
    echo "Not stichting documents together as both --double=true and --front=true"
    exit 0
fi

if [ "${DOUBLE}" = true ] && [ "${BACK}" = true ] && [ ! -f "${DIR}/${FILE_PREFIX}-p01.tiff" ]; then
    echo "Not stichting documents together, missing front pages"
    exit 0
fi

# Stitch documents together
SCANNED_DOCS_COUNT=$(find "${DIR}" -iname "${FILE_PREFIX}-*.${FILE_FORMAT}" 2>/dev/null | wc -l)
if [ "$SCANNED_DOCS_COUNT" -gt 1 ]; then
    COMBINED_DOC="${DIR}/${FILE_PREFIX}.${FILE_FORMAT}"
    echo "Stitching together single document ${COMBINED_DOC}"
    if [ -f "${COMBINED_DOC}" ]; then
        echo "Aborting, file already exists"
        exit 1
    fi
    tiffcp "${DIR}/${FILE_PREFIX}"-*.tiff "${COMBINED_DOC}" && rm -f "${DIR}/${FILE_PREFIX}"-*.tiff
elif [ "$SCANNED_DOCS_COUNT" -eq 1 ]; then
    echo "Single page scanned, no stiching"
else
    echo "No TIFF files scanned"
fi
