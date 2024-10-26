#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

file_types = {
    "html": "text/html",
    "htm":  "text/html",
    "shtml": "text/html",
    "shtm": "text/html",
    "ssi":  "text/html",
    "gif":  "image/gif",
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "bmp":  "image/bmp",
    "ico":  "image/x-icon",
    "class": "application/octet-stream",
    "cls":  "application/octet-stream",
    "js":   "application/javascript",
    "ram":  "application/javascript",
    "css":  "text/css",
    "swf":  "application/x-shockwave-flash",
    "xml":  "text/xml",
    "xsl":  "application/pdf",
    "pdf":  "text/xml",
    "json": "application/json",
    "svg":  "image/svg+xml",
    "woff2": "text/plain"
}

response_types = {
  200: "HTTP/1.0 200 OK",
  400: "HTTP/1.0 400 Bad Request",
  404: "HTTP/1.0 404 File not found",
  501: "HTTP/1.0 501 Not Implemented",
}

PAYLOAD_ALIGNMENT = 4
HTTPD_SERVER_AGENT = "lwIP/2.2.0d (http://savannah.nongnu.org/projects/lwip)"
LWIP_HTTPD_SSI_EXTENSIONS = [".shtml", ".shtm", ".ssi", ".xml", ".json"]

def process_file(input_dir, file):
    results = []

    # Check content type
    content_type = file_types.get(file.suffix[1:].lower())
    if content_type is None:
        raise RuntimeError(f"Unsupported file type {file.suffix}")

    # file name
    file_name = str(file.relative_to(input_dir))
    file_name = file_name.replace("\\", "/")
    data = f"/{file_name}\x00"
    comment = f"\"/{file_name}\" ({len(data)} chars)"
    while(len(data) % PAYLOAD_ALIGNMENT != 0):
        data += "\x00"
    results.append({'data': bytes(data, "utf-8"), 'comment': comment});

    # Header
    response_type = 200
    for response_id in response_types:
        if file.name.startswith(f"{response_id}."):
            response_type = response_id
            break
    data = f"{response_types[response_type]}\r\n"
    comment = f"\"{response_types[response_type]}\" ({len(data)} chars)"
    results.append({'data': bytes(data, "utf-8"), 'comment': comment});

    # user agent
    data = f"Server: {HTTPD_SERVER_AGENT}\r\n"
    comment = f"\"Server: {HTTPD_SERVER_AGENT}\" ({len(data)} chars)"
    results.append({'data': bytes(data, "utf-8"), 'comment': comment});

    if file.suffix not in LWIP_HTTPD_SSI_EXTENSIONS:
        # content length
        file_size = file.stat().st_size
        data = f"Content-Length: {file_size}\r\n"
        comment = f"\"Content-Length: {file_size}\" ({len(data)} chars)"
        results.append({'data': bytes(data, "utf-8"), 'comment': comment});

    # content type
    data = f"Content-Type: {content_type}\r\n\r\n"
    comment = f"\"Content-Type: {content_type}\" ({len(data)} chars)"
    results.append({'data': bytes(data, "utf-8"), 'comment': comment});

    # file contents
    data = file.read_bytes()
    comment = f"raw file data ({len(data)} bytes)"
    results.append({'data': data, 'comment': comment});

    return results;

def process_file_list(fd, input):
    data = []
    fd.write("#include \"fsdata.h\"\n")
    fd.write("\n")
    # generate the page contents
    input_dir = None
    for name in input:
        file = Path(name)
        if not file.is_file():
            raise RuntimeError(f"File not found: {name}")
        # Take the input directory from the first file
        if input_dir is None:
            input_dir = file.parent
        results = process_file(input_dir, file)

        # make a variable name
        var_name = str(file.relative_to(input_dir))
        var_name = var_name.replace(".", "_")
        var_name = var_name.replace("/", "_")
        var_name = var_name.replace("\\", "_")
        data_var = f"data_{var_name}"
        file_var = f"file_{var_name}"

        # variable containing the raw data
        fd.write(f"static const unsigned char {data_var}[] = {{\n")
        for entry in results:
            fd.write(f"\n    /* {entry['comment']} */\n")
            byte_count = 0;
            for b in entry['data']:
                if byte_count % 16 == 0:
                    fd.write("    ")
                byte_count += 1
                fd.write(f"0x{b:02X},")
                if byte_count % 16 == 0:
                    fd.write("\n")
            if byte_count % 16 != 0:
                 fd.write("\n")
        fd.write(f"}};\n\n")

        # set the flags
        flags = "FS_FILE_FLAGS_HEADER_INCLUDED"
        if file.suffix not in LWIP_HTTPD_SSI_EXTENSIONS:
            flags += " | FS_FILE_FLAGS_HEADER_PERSISTENT"
        else:
            flags += " | FS_FILE_FLAGS_SSI"

        # add variable details to the list
        data.append({'data_var': data_var, 'file_var': file_var, 'name_size': len(results[0]['data']), 'flags': flags})

    # generate the page details
    last_var = "NULL"
    for entry in data:
        fd.write(f"const struct fsdata_file {entry['file_var']}[] = {{{{\n")
        fd.write(f"    {last_var},\n")
        fd.write(f"    {entry['data_var']},\n")
        fd.write(f"    {entry['data_var']} + {entry['name_size']},\n")
        fd.write(f"    sizeof({entry['data_var']}) - {entry['name_size']},\n")
        fd.write(f"    {entry['flags']},\n")
        fd.write(f"}}}};\n\n")
        last_var = entry['file_var']
    fd.write(f"#define FS_ROOT {last_var}\n")
    fd.write(f"#define FS_NUMFILES {len(data)}\n")

def run_tool():
    input = []
    output = ''

    parser = argparse.ArgumentParser(prog="makefsdata.py", description="Generates a source file for the lwip httpd server")
    parser.add_argument(
        "-i",
        "--input",
        help="Input files to add as http content",
        required=False,
        nargs='+'
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Name of the source file to generate",
        required=False,
    )
    args = parser.parse_args()    
    
    if args.input is None:
        walk_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'fs')
        print('Use default input path = ' + walk_dir)
        for root, subdirs, files in os.walk(walk_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                input.append(file_path)
                print('\t- file %s (full path: %s)' % (file_name, file_path))
    else:
        input = args.input

    if args.output is None:
        output = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'fsdata.c')
        print('Use default output = ' + output)
    else:
        output = args.output

    with open(output, "w") as fd:
        process_file_list(fd, input)

if __name__ == "__main__":
    run_tool()