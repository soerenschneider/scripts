#!/usr/bin/env python3

import os
import shutil
import argparse
import glob
import re

def extract_number(filename):
    base_name = os.path.splitext(filename)[0]
    match = re.search(r'(\d+)', base_name)
    if match:
        return match.group(1)
    else:
        raise ValueError(f"Filename {filename} does not contain a numeric part")

def move_photos(first_photo, last_photo, dirname):
    # Extract the number from the filenames
    start_num_str = extract_number(first_photo)
    end_num_str = extract_number(last_photo)

    # Determine the length of the numeric part
    num_length = len(start_num_str)

    # Convert the extracted numbers to integers
    start_num = int(start_num_str)
    end_num = int(end_num_str)

    # Extract the prefix to handle different prefixes
    prefix = re.match(r'([a-zA-Z]+)', first_photo).group(1)

    # Create the destination directory if it doesn't exist
    if not os.path.exists(dirname):
        os.makedirs(dirname)

    # Iterate through the range of numbers and move the corresponding files
    for num in range(start_num, end_num + 1):
        num_str = f"{num:0{num_length}d}"  # Format the number with leading zeros
        pattern = f"{prefix}{num_str}.*"   # Look for files with the current number and any extension
        for filepath in glob.glob(pattern):
            if os.path.exists(filepath):
                shutil.move(filepath, dirname)
                print(f"Moved {filepath} to {dirname}")
            else:
                print(f"{filepath} does not exist")

def main():
    parser = argparse.ArgumentParser(description='Move photos from a range to a specified directory.')
    parser.add_argument('first_photo', type=str, help='The first photo in the range (e.g., ARW0001.ext)')
    parser.add_argument('last_photo', type=str, help='The last photo in the range (e.g., ARW0050.ext)')
    parser.add_argument('dirname', type=str, help='The name of the directory to move photos to')

    args = parser.parse_args()

    move_photos(args.first_photo, args.last_photo, args.dirname)

if __name__ == '__main__':
    main()

