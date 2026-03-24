#!/bin/bash

mkdir project && cd project
bunx @clanker-guru/openspec --version
bunx @clanker-guru/openspec init --tools ${TOOLS:-claude}
