=======
History
=======

1.0.1 (2022-02-02)
------------------

* Fixed issue when the primary part of a model was missing (cgam, but no cga)
* Fixed an issue in texture extraction when _not_ converting or un-splitting textures
* Improved Blender compatability checking and Blender version handling
* Support for Blender 3.0


1.0.0 (2022-01-18)
------------------

* First major version
* Texture conversion utilities
* Improved CLI commands
* Refactored and improved Star Engine format handling
* Star Citizen Blueprint (scbp) system
* Plugin framework
* Blender add-on supporting scbp imports
* Audio (wwise) system handling
* P4K converters to enable auto-conversion of proprietary formats during export (textures, models, etc.)
* Prefab Library Manager
* Object Container Manager
* Material Library processor (mtl)
* Launcher utilities to auto-discover installed SC versions


0.1.7 (2021-04-02)
------------------

* Added Datacore v5 support


0.1.6 (2020-12-30)
------------------

* Moved to GitLab
* Updated docs
* Improved filename searches in P4Ks
* Dataforge records can now be outputed to xml as well
* Improved pretty printing of XML output

0.1.5 (2020-12-9)
-----------------

* Improved path and error handling

0.1.3 (2020-12-06)
------------------

* Added SC profile dumping (actionmaps)
* New `StarCitizen` class convenience wrapper around the installation dir
* Support for looking up localization strings
* Dataforge fixes

0.1.2 (2020-05-20)
------------------

* Initial commit
