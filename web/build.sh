#!/bin/sh
# Wraps wifi-atlas.html (the page body) into a standalone index.html you can open from disk.
cd "$(dirname "$0")"
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
  cat wifi-atlas.html
  printf '\n</body>\n</html>\n'
} > index.html
echo "wrote web/index.html"
