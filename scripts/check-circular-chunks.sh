#!/bin/bash
# Build guard script - Check for circular chunk warnings in build output

set -e

echo "Running npm build and checking for circular chunk warnings..."

# Run build and capture output
BUILD_OUTPUT=$(npm run build 2>&1)

# Check for circular chunk warnings
if echo "$BUILD_OUTPUT" | grep -q "Circular chunk"; then
    echo "ERROR: Circular chunk dependencies detected in build!"
    echo "$BUILD_OUTPUT" | grep "Circular chunk"
    exit 1
fi

# Check for build errors
if echo "$BUILD_OUTPUT" | grep -q "error TS"; then
    echo "ERROR: TypeScript errors detected in build!"
    exit 1
fi

echo "Build check passed - no circular chunk warnings found"
exit 0
