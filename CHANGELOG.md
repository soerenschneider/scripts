# Changelog

## [1.6.0](https://www.github.com/soerenschneider/scripts/compare/v1.5.0...v1.6.0) (2022-06-02)


### Features

* add offset times, reading args from config file ([14980ce](https://www.github.com/soerenschneider/scripts/commit/14980ce0f7f6c59dc825f63b6d2dbed41a1c4542))
* follow symlinks ([eec121b](https://www.github.com/soerenschneider/scripts/commit/eec121b2241f7a66481635cccfb10734e061353b))
* print config values ([d7d62e5](https://www.github.com/soerenschneider/scripts/commit/d7d62e526293b84add42135ebcddb6c769066a12))


### Bug Fixes

* call correct script ([08046cd](https://www.github.com/soerenschneider/scripts/commit/08046cd828124c4e9ef5307afff0510176016cec))
* use correct range for validation ([f33c73e](https://www.github.com/soerenschneider/scripts/commit/f33c73e699996519668ed3e87c6465f87ade8dbb))

## [1.5.0](https://www.github.com/soerenschneider/scripts/compare/v1.4.0...v1.5.0) (2022-03-24)


### Features

* Add daily script ([d6af26e](https://www.github.com/soerenschneider/scripts/commit/d6af26e349bd747d95252b60284923ed406db644))
* add taskwarrior-gen-kanban script ([561d59a](https://www.github.com/soerenschneider/scripts/commit/561d59a0b05859619103e0725bd0ee2addce9004))
* Compatibility with vault tokens v1.10 ([70b367f](https://www.github.com/soerenschneider/scripts/commit/70b367fc0b7556e61b1b290070c9f38609bb3968))

## [1.4.0](https://www.github.com/soerenschneider/scripts/compare/v1.3.0...v1.4.0) (2022-03-09)


### Features

* add script to get a random wallpaper ([dbb5a1c](https://www.github.com/soerenschneider/scripts/commit/dbb5a1c194432b3c4d265622d8579ac8e24c588f))

## [1.3.0](https://www.github.com/soerenschneider/scripts/compare/v1.2.1...v1.3.0) (2022-02-13)


### Features

* Add further commands ([939f04a](https://www.github.com/soerenschneider/scripts/commit/939f04acde38bab76c4e0150559b7b93add89ad9))
* Add success metric ([24f3ab6](https://www.github.com/soerenschneider/scripts/commit/24f3ab657c5687985410df93e7a5df13db767d37))
* first draft of script to reset wg peers ([025cfa1](https://www.github.com/soerenschneider/scripts/commit/025cfa1d68ff0640e8da0c1fa3be5e8f1aa0e97f))


### Bug Fixes

* fix help message ([9138475](https://www.github.com/soerenschneider/scripts/commit/9138475c291da8bffbed19dad57769e541505905))
* fix missing parameter ([2e32c30](https://www.github.com/soerenschneider/scripts/commit/2e32c3018b038dca4734c9262ec5f0082e3ff380))
* fix timestamp generation ([f4be30d](https://www.github.com/soerenschneider/scripts/commit/f4be30de2195144ca8d2e96409b72673f9668726))
* require both role_id and role_name for cmd rotate-secret-id ([82db6d2](https://www.github.com/soerenschneider/scripts/commit/82db6d25e9708cd6078f7ee6f4f633b325d42d5c))
* use correct arg ([8a1f08a](https://www.github.com/soerenschneider/scripts/commit/8a1f08ac66e48189f08475927b371a38a133635c))
* write correct timestamp ([3fde058](https://www.github.com/soerenschneider/scripts/commit/3fde0582d41ce166eb083f2b0d95f16c26001676))

### [1.2.1](https://www.github.com/soerenschneider/scripts/compare/v1.2.0...v1.2.1) (2022-01-24)


### Bug Fixes

* don't create password locally by default when rotation secret_id ([3bf57fc](https://www.github.com/soerenschneider/scripts/commit/3bf57fcdc4cd72127cf712347a92f99e72ffee7a))
* wrap correct output ([b3688f3](https://www.github.com/soerenschneider/scripts/commit/b3688f339e285c4d75834e3956482642b9e79768))

## [1.2.0](https://www.github.com/soerenschneider/scripts/compare/v1.1.0...v1.2.0) (2022-01-23)


### Features

* Add command to print role info ([eef0aac](https://www.github.com/soerenschneider/scripts/commit/eef0aac178f16722e27bf11f66033885c802bb38))
* call out unknown config values ([97bf833](https://www.github.com/soerenschneider/scripts/commit/97bf833f325edc6327d684224950660756bc7be6))
* implement backoff ([9a0cf96](https://www.github.com/soerenschneider/scripts/commit/9a0cf960a11782c08b0d0a3ab311f4f6e246cf59))


### Bug Fixes

* use correct accessor for output ([7f7e7c6](https://www.github.com/soerenschneider/scripts/commit/7f7e7c696543aececc605d00a5bc033fa727b0da))

## [1.1.0](https://www.github.com/soerenschneider/scripts/compare/v1.0.0...v1.1.0) (2022-01-03)


### Features

* Add check command ([878cb45](https://www.github.com/soerenschneider/scripts/commit/878cb45ec9585480f5cdf994e02f9e88126ef195))
* Add first restic-backup script ([dfc36d1](https://www.github.com/soerenschneider/scripts/commit/dfc36d1c1cdad57cf187e040b30dc1991bd15f13))
* Add prune command ([27db23a](https://www.github.com/soerenschneider/scripts/commit/27db23a87fdd2b539e84015f4b6ba55c51a00833))


### Bug Fixes

* default metrics dir ([1ae09b0](https://www.github.com/soerenschneider/scripts/commit/1ae09b0e4c551b580a7e379558e39f1d57ac5558))
* fix metric format error ([6bc8654](https://www.github.com/soerenschneider/scripts/commit/6bc865491f63e9bf6b11e8e8ecb9ad6047109c70))

## 1.0.0 (2021-10-31)


### Bug Fixes

* fix wrong file corruption with requests library ([5857172](https://www.github.com/soerenschneider/scripts/commit/585717204f42d4a6134f7ac8ae9e50bbe54c6354))
