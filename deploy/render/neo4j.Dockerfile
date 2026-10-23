FROM neo4j:5-community

COPY start-neo4j.sh /opt/ignitic/start-neo4j.sh

# Preserve the upstream init process, signal forwarding and bootstrap logic.
ENTRYPOINT ["tini", "-g", "--", "/bin/sh", "/opt/ignitic/start-neo4j.sh"]
CMD ["neo4j"]
