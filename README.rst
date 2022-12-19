scdatatools
===========

Python API for interactive with the data files in Star Citizen.

.. warning:: This tool suite is in it's very early stages and will change often.

* Free software: MIT license
* Documentation: https://scdatatools.readthedocs.io.

Hey! Listen!
------------

This tool is in **very** early development. The CLI is a WIP and may not be completely plumbed up yet.
If you'd like to help out and know Python, try out the API a little bit and see if you run into errors parsing
files! We're also at the stage that feature/usability feedback would be much appreciated.


Features
--------

* cli interface
* TODO


CLI Examples
------------

.. code-block:: bash

    usage: scdt [-h] [--verbose] [--stderr] [--command-timeout COMMAND_TIMEOUT] [command] ...

    positional arguments:
      [command]             Subcommand to run, if missing the interactive mode is started instead.
        cryxml-to-json      Convert a CryXML file to JSON
        cryxml-to-xml       Convert a CryXML file to xml
        unforge             Convert a DataForge file to a readable format
        unp4k               Extract files from a P4K file
        actionmap           Dumps the default profile action map (keybinds) as JSON


API Examples
------------

Read a DataForge database (.dcb)

.. code-block:: python

    from scdatatools.forge import DataCoreBinary
    dcb = DataCoreBinary('research/Game.dcb.3.9.1-ptu.5229583')
    # Find all records containing a patterh
    jav_records = dcb.search_filename('*javelin.xml')
    print(dcb.dump_record_json(jav_records[-1]))

Use the `StarCitizen` class:

.. code-block:: python

    from scdatatools.sc import StarCitizen
    sc = StarCitizen('D:/Path/To/LIVE')
    sc.p4k.search('idris')
    sc.datacore.records[0]

    # Read all entities
    sc.attachable_component_manager.load_attachable_components()
    cmgr = sc.attachable_component_manager
    # Get all entity types
    cmgr.by_type.keys()
    # Get the English name of the first torso armor
    list(cmgr.by_type['Char_Armor_Torso'])[0].label
    # Get the labels of all items in the family 'behr_rifle_ballistic_01'
    [x.label for x in cmgr.by_geom[cmgr.attachable_components['behr_rifle_ballistic_01'].geom_hash]]
    # Find entities by in-game name
    [x for x in cmgr.attachable_components.values() if 'Drake Cutlass' in x.label]


.. image:: https://gitlab.com/scmodding/frameworks/scdatatools/-/raw/devel/docs/assets/MadeByTheCommunity_Black.png
  :width: 128
  :alt: Made By the Star Citizen Community

This project is not endorsed by or affiliated with the Cloud Imperium or Roberts Space Industries group of companies.
All game content and materials are copyright Cloud Imperium Rights LLC and Cloud Imperium Rights Ltd..  Star Citizen®,
Squadron 42®, Roberts Space Industries®, and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC.
All rights reserved.
