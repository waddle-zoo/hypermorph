# Hypermorph

Hypermorph is the public release distribution for [Hyperset](https://github.com/waddle-zoo/hyperset), an AI-native analytics context system.

This repository is intentionally a **publication mirror**, not the development source. Releases are promoted manually from reviewed, tagged commits in the private `waddle-zoo/hyperset` repository. Gas Town works only on Hyperset and has no authority here.

## What is published here

When a release is requested, maintainers copy the complete source tree for the selected Hyperset release and publish:

- the immutable release tag and source commit;
- release notes with the originating Hyperset commit;
- generated artifacts, when applicable;
- an SBOM and build provenance/attestation.

Until the first promotion, this repository contains only this publication contract.

## Trust boundary

Public issues are disabled and changes are not accepted directly here. Development, review, and merging happen in Hyperset. A release is built from an exact reviewed commit - not from a pull-request branch - and then pushed by a narrowly scoped release identity.

Consumers should verify the release tag, artifact digest, and provenance before using a published build. The release history is the public record; Hyperset remains the source of truth.

## Contributing

Please use the [Hyperset repository](https://github.com/waddle-zoo/hyperset) for documentation, fixes, and proposals. Do not open a pull request against this mirror.

## License

Apache License 2.0. See [LICENSE](LICENSE).
