# OpenSearch 2.17.1 with the analysis-icu plugin (Telugu tokenization/folding for BM25).
#
# analysis-icu is not in the stock image. It is installed from the local zip next to this file
# (copied from /home/admin1/RAG/docker/) rather than by URL: behind this network's TLS-intercepting
# proxy the JVM in the build cannot reach artifacts.opensearch.org. The plugin version MUST match
# the FROM tag. Refresh the zip with:
#   curl -fSLO https://artifacts.opensearch.org/releases/plugins/analysis-icu/2.17.1/analysis-icu-2.17.1.zip
FROM opensearchproject/opensearch:2.17.1

COPY analysis-icu-2.17.1.zip /tmp/analysis-icu.zip
RUN /usr/share/opensearch/bin/opensearch-plugin install --batch file:///tmp/analysis-icu.zip
