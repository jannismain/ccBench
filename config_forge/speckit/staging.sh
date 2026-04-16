#!/bin/bash

uvx --from git+https://github.com/github/spec-kit.git specify version
uvx --from git+https://github.com/github/spec-kit.git specify init project --ai ${AGENT:-claude} --script sh
