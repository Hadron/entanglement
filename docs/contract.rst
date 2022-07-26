.. _contract:

##########################################
Entanglement Security and Policy Contract
##########################################

**Entanglement's** security and policy depend on a complex interaction of classes.  This describes the contract between these classes and callers of the Entanglement system.  In particular, this contract describes how to control:

* What objects are sent where in an Entanglement system (*should_send*)

* Which updates are received and accepted at various points in the system (*should_listen)*

* How to get information about a connected endpoint to make policy decisions about it.



Classes and Their Responsibilities
==================================

`Synchronizable`
----------------

A Synchronizable is some object that can be synchronized.
A Synchronizable type is responsible for any policy specific to that type.  In general it is best to minimize type-specific policy and instead put policy on a registry or destination; see below.


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

* Path enforcement (for example making sure updates travel toward object owners) and that non-owners do not impersonate owners.

When used with an SQL instantiation, the *cls* parameter of :func:`!entanglement.sql.base.sync_manager_destinations()` can be used to override which class is used for SyncDestination to allow customization.

`SyncRegistry`
--------------

A SyncRegistry represents a coherent protocol comprized of a schema of `Synchronizable` types and operations on those types.  This is the primary point for policy at that protocol level and policy about those operations:

* ACL mechanisms might go here if not on a SyncDestination

* Logic about what to do with an object after it is synchronized--the logic specific to an operation--goes here.


`SyncProtocol`
--------------

The protocol is mostly an internal class.  It does have a few methods
that allow querying the remote certificate for TLS-based connections.
The protocol also supports storing information about the request that
initiated a websocket connection in websocket applications.  As an
example, user identity or session identity via cookies can be accessed
on the `ws_handler` protocol.


No application should need to import from ``entanglement.protocol``.
This class performs no ACL checking.  On send, all policy is enforced before calling the object synchronization method.  On receive. as soon as the JSON is decoded, the resulting dictionary is turned over to the SyncManager.

Applications MUST NOT call object synchronization methods on a protocol.

`SqlSyncRegistry`
-----------------

This is responsible for defining SQL operations:

* delete: Deletes an object

* sync: floods a new or existing object's state out to recipients

* forward: requests a set of changes (or a new object) be synchronized.  That is, a request to an object owner to consider creating or updating an object

Overview
========
Both the *should_send* and *should_listen* workflows are codified in `SyncManager`::
      def should_send(self, obj, destination, registry, sync_type, **info):
        '''
        1. destination.should_send( obj, **info):
        2. registry.should_send( obj, **info):
        3. obj.sync_should_send(**info):
        '''

    def should_listen(self, msg, cls, **info):
        '''
        1. sender.should_listen(msg, cls, **info) is not True:
        2. registry.should_listen(msg, cls, **info)is not True:
        3. cls.sync_should_listen(msg, **info) is not True:
            * here cls is the entanglement object class
        '''

    def should_listen_constructed(self, obj, msg, **info):
        '''
        1. info['registry'].should_listen_constructed(obj, msg, **info)
        2. obj.sync_should_listen_constructed(msg, **info)
        '''

Should_listen and Should_listen_constructed
-------------------------------------------

For the receive workflow, two sets of methods are provided:

* *should_listen*

* *should_listen_constructed*

The first of these receives a message dictionary containing keys and their encoded values.  The class is available, but an instance of `Synchronizable` is not.
The advantage of performing checks at this stage is that the attack surface can be reduced and performance increased if objects are rejected before they are constructed.

The disadvantage is that many checks are easy to perform against constructed objects:

* new values can be compared to old values

* Database relationships are available; for example it is easy to look at the owner of an object.



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



#. Destination::should_send
#. SyncRegistry::should_send
#. Object::sync_should_send

The *should_send* methods can return ``True`` or ``False`` or raise.
