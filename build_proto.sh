#!/bin/bash

protoc -I=proto/core --python_out=. proto/core/message.proto
protoc -I=proto/light --python_out=. proto/light/light.proto
