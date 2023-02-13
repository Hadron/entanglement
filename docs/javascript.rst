.. highlight:: javascript

.. default-domain:: js

Entanglement Javascript Support
===============================

Entanglement has a Javascript interface that allows websocket clients to connect to a Python server and provides two-way synchronization of objects.

* The :ref:`javascript schema generator <javascript:schema>` is used to export the Python schema to Javascript.

* This schema is imported into a javascript project typically built with Webpack.

* The schema is attached to a :class:`SyncRegistry`
* Persistence is set up.
* :ref:`filters <javascript:filters>` and relations are used to be notified of synchronized objects.

.. _javascript:schema:

Javascript Schema Generator
***************************

The set of primary keys and atcributes associated with :py:class:`~entanglement.Synchronizable` classes is exported from the Python code to Javascript.
If a class has no special behavior in Javascript, then Entanglement will automatically generate a Javascript class to represent the *Synchronizable*.  In typical usage, :ref:`javascript classes <javascript:registering>` are required to customize the behavior of the synchronized object.  The automatically generated class can serve as a base class to set up basic properties and primary keys.  Unfortunately, because of a :ref:`bug <javascript:persist_bug>`, the automatically generated base classes will not work for persistent objects unless a custom class extending PersistentSynchronizable is created for each synchronized class.


First, in your Python code, attach a :py:class:`SyncRegistry` to a  javascript schema.

.. code-block:: python

    from entanglement.javascript_schema import javascript_registry
    from entanglement import SyncRegistry, Synchronizable

    registry = SyncRegistry()

    class SomeSync(Synchronizable):

        sync_registry = registry

        # some code

    javascript_registry(registry, "our_registry.js")

.. py:module:: entanglement.javascript_schema

.. py:function:: javascript_registry(registry:SyncRegistry, schema:str)

    :param registry: the :py:class:`~entanglement.SyncRegistry` that should be made available in javascript

    :param schema: The filename to write the javascript schema to.

Then, run the schema generator:

.. code-block:: none

    python3 -m entanglement.javascript_schema packages_containaing_registries

For example if ``project.entanglement.schema``  containes several sync registries, you might run:

.. code-block:: none

    python3 -mentanglement.javascript_schema project.entanglement.schema

That would produce schema for any call to :py:func:`javascript_regstry` in the ``project.entanglement.schema`` package.  Generally, Entanglement Javascript projects also require the ``sql_meta.js`` schema produced by ``entanglement.sql.internal``.

Using a Schema
**************

Assume that entanglement schemas are stored in ``./entanglement_schemas``..  There is one schema called ``project.js`` and the ``sql_meta.js`` schema.
A project using Entanglement might look something like::

    import {SyncManager, SyncRegistry} from "entanglement";
    import {SyncOwner, relationship, setupPersistence} from "entanglement/persistence";
    import {filter, FilterBase} from "entanglement/filter";
    import MetaSchema from "../entanglement_schemas/sql_meta";
    import Schema from "../entanglement_schemas/project"

Each schema exports a default function called register_schema:

.. function:: register_schema(registry)

    :param registry: The :class:`SyncRegistry` to attach this schema to.

    Typical usage is to import the function and call it on a registry::

        import schema from "./entanglement_schemas/schema"
        const registry = new SyncRegistry()
        schema(registry)

So, going back to our sample project, we would register the two schemas::

    registry = new SyncRegistry()
    MetaSchema(registry)
    Schema(registry)

And then set up persistence::

    setupPersistence(registry)

.. function:: setupPersistence(registry)

    :param registry: a :class:`SyncRegistry` on which to enable persistence

    After the ``sql_meta`` schema is attached to a registry, *setupPersistence* must be called to attach javascript implementations to classes defined in the schema.

    
.. _javascript:registering:

Registering Custom Classes
**************************

To define custom behavior, define a Javascript class with the same name as the Python class.  Ideally this class extends :class:`PersistentSynchronizable`::

    class SomeSynchronizable extends PersistentSynchronizable {

        // Extra methods can go here

        }

    // and register the class
    registry.rregister(SomeSynchronizable)

If a class is :meth:`registered <SyncRegistry.register>` that is  in a schema attached to the registry, then:

* Attributes are made available for all sync attributes.

* Primary keys are handled.

.. _javascript:persist_bug:

.. warning::

    There is support for schema classes that are auto generated rather than calling register explicitly.  Also, there is support for using an application specific base hierarchy so classes do not need to extend *PersistentSynchronizable*.  Unfortunately both items are buggy.  In particular,  if a class does not extend *PersistentSynchronizable*, then only the methods of *Synchronizable* will be included.  As a result, each time an instance is synchronized, a new instance will be generated.  All the :ref:`filters <javascript:filters>` will fail.  Also methods required for updating, creating and deleting objects will not be present.

