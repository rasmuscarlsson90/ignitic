#!/bin/sh
set -eu

: "${NEO4J_PASSWORD:?Render must generate a Neo4j password}"
export NEO4J_AUTH="neo4j/$NEO4J_PASSWORD"
exec /startup/docker-entrypoint.sh "$@"
