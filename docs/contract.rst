##########################################
Entanglement Security and Policy Contract
##########################################

**Entanglement's** security and policy depend on a complex interaction of classes.  This describes the contract between these classes and callers of the Entanglement system.

Classes and Their Responsibilities
==================================

`SyncManager` and `SyncServer`
------------------------------


The :any:`SyncManager` is responsible for the overall connection.  It acts as a control point for all incoming and outgoing object synchronizations.

It's rare that a user would need to subclass `SyncManager` or `SyncServer`.  The only probable case would be if  an application needed to take advantage of that choke point.  As an example that is one place that MAC policies could be checked in a defense-in-depth MAC enforcement strategy.

The SyncManager is not expected to know about the semantics of :any:`Synchronizable` or `SyncRegistry` objects.

`SyncDestination`
-----------------

The SyncDestination represents a connection to a  single endpoint.  It's the ideal  place to enforce policy about that destination that does not depend significantly on the semantics of the objects being synchronized:

* Preventing loops

* Limiting users to see only what objects they are authorized to see

* Primary point for MAC policy

* Path enforcement (for example making sure updates travel toward object owners)

When used with an SQL instantiation, the *cls* parameter of :func:`!entanglement.sql.base.sync_manager_destinations()` can be used to override which class is used for SyncDestination to allow customization.

`SyncRegistry`
--------------

A SyncRegistry represents a coherent protocol comprized of a schema of `Synchronizable` types and operations on those types.  This is the primary point for policy at that protocol level and policy about those operations:

* ACL mechanisms might go here if not on a SyncDestination

* Logic about what to do with an object after it is synchronized--the logic specific to an operation--goes here.

`Synchronizable`
----------------

A Synchronizable type is responsible for any policy specific to that type.

`SyncProtocol`
--------------

An entirely internal class used for the network operations.  No application should need to import from ``entanglement.protocol``.
This class performs no ACL checking.  On send, all policy is enforced before calling the object synchronization method.  On receive. as soon as the JSON is decoded, the resulting dictionary is turned over to the SyncManager.

Applications MUST NOT call object synchronization methods on a protocol.

`SqlSyncRegistry`
-----------------

This is responsible for defining SQL operations:

* delete: Deletes an object

* sync: floods a new or existing object's state out to recipients

* forward: requests a set of changes (or a new object) be synchronized.  That is, a request to an object owner to consider creating or updating an object


Receive Workflow
================

The `SyncProtocol` calls :meth:`manager._sync_receive <entanglement.network.SyncManager._sync_receive>`.  This method:

#. Validates the message contains expected metadata and does not contain unknown metadata.

#. Looks up the type being synchronized in the registries associated with the manager.  If no such class is found, the object is immediately rejected.

#. Calls the manager's :meth:`~.SyncManager.should_listen` method, which:

   #. Calls the `destination's <SyncDestination>` :meth:`~.SyncDestination.should_listen` method.

   #. Calls the registry's should_listen method

   #. Calls the class's sync_should_listen method

#. Calls the registry's sync_context method to get a context in which to perform the operation.  (used as a context manager)

#. Calls the class's sync_construct method to construct an instance of the class from the message.

#.  Calls the manager's should_listen_constructed method.  Some operations are easier to implement with a constructed object than only with a message.  Where possible it is better to implement checks in should_listen rather than should_listen_constructed to minimize the attack surface.  However it is generally better to avoid duplicating code and use should_listen_constructed for tasks that cannot easily be done in should_listen.  This method:

   #. Calls the object's should_listen_constructed method.

#. Call the class's sync_receive_constructed method to fill in non-primary-key attributes from the message.

#. Calls the registry's sync_receive method to  perform the operation.

#. Exits the context manager.

All should_listen methods must return True for the object to be received.
The should_listen methods  are passed the message dictionary not a constructed object.  This is valuable in that it provides an opportunity to examine the object before much of the class code is run.  The attack surface is reduced.  However it may make certain policy checks more difficult because the object is not available.  The registry and object can perform additional policy checks in the sync_constructed and sync_receive_constructed and operation-specific methods, throwing an exception if desired.  That may ease implementation but provides a wider attack surface.

Send workflow
=============
