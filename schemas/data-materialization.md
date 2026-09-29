# Harmony Data Materialization Contract

Expansion owns data acquisition required by experiments.

The materialization process is:

SOURCE URL
→ DOWNLOAD BYTES
→ VERIFY OPTIONAL UPSTREAM CHECKSUM
→ COMPUTE SHA-256
→ PERSIST IMMUTABLE MANIFEST
→ TRACK IDENTITY IN CONTRACTION

The upstream checksum is evidence supplied by the source. The SHA-256 computed from
the bytes actually materialized by Expansion is Harmony's primary content identity.

A dataset is not considered acquired merely because a URL or catalog record exists.
Acquisition is complete only after the bytes have been materialized and fingerprinted.

Contraction tracks the resulting identity and provenance; it does not perform the
experimental download itself.
