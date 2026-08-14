# Vendored pyezvizapi

This directory contains the `pyezvizapi` 1.0.5.0 source used by EZVIZ Cloud
Auto.

The package is intentionally imported as
`custom_components.ezviz_cloud.vendor.pyezvizapi` instead of the global
`pyezvizapi` module. Home Assistant's built-in EZVIZ integration can therefore
keep its own compatible `pyezvizapi` version without replacing this one.

The upstream package is published by Renier Moorcroft under the Apache
License 2.0. The accompanying `LICENSE` and `LICENSE.md` files are retained
with the vendored source.
