#!/bin/bash

protoc -I=proto/ --python_out=. proto/core/message.proto
protoc -I=proto/ --python_out=. proto/light/light.proto
protoc -I=proto/ --python_out=. proto/build/build.proto
protoc -I=proto/ --python_out=. proto/audio/audio.proto
