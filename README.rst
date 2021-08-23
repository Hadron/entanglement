Entanglement
============

**Entanglement** is a state synchronization protocol for Python and
Javascript objects loosely based on lessons learned by the `Mosh
<https://mosh.org/>`__ developers.  You can read our `documentation <https://entanglement.readthedocs.io/>`__.

Entanglement was motivated  by a desire to synchronize user interface objects across the world with wildly variable network conditions.  Within a room, bandwidth and latency may be low.  Between sites, latency will be higher and bandwidth may be limited over satellite or radio links.  Connectivity may be intermittent.

Security and policy flexibility are an important motivation for
Entanglement. Organizations may not be interested in sharing all
objects equally.  Consider a cyber training exercise as an example.
Entanglement is used to share state about access to virtual machines so that participants in the exercise can hand off machines, ask for help, and follow each others’ progress.  There is a red team responsible for trying to attack a defended
network.  A blue team is responsible for defending the network.  The
exercise staff and range team need to have visibility into the entire
exercise.  Yet it would give the blue team an unfair advantage if they
could see what the red team was doing.  Entanglement provides policy
control points so that for example every team can share state with the
exercise staff and range team, but the red and blue teams remain
isolated.

Entanglement is not a database synchronization mechanism but  contrasting Entanglement with database synchronization helps understand the novel aspects of the system.

* Database synchronization focuses on rapidly replicating transactions across sites while maintaining referential integrity.

  * Entanglement focuses on sending the most recent state; intermediate states are sacrificed when necessary for faster convergence.  Consider synchronizing the position of a mouse cursor.  When the network is fast, it is desirable for the mouse to track in real time.  When that is not possible, it is often better for the mouse cursor to skip to its current position rather than getting further behind as the cursor slowly replays its path.  Entanglement makes this sort of convergence easy by allowing intermediate states to be discarded.  Entanglement does support state changes that must always be transmitted, but this is not the default.

  * Entanglement sacrifices referential integrity in order to provide faster convergence of important state.  Entanglement can be configured to preserve referential integrity if that is required.

* Database synchronization typically trusts all replicas equally.  All objects are sent to all replicas.

  * Entanglement provides application entry points to decide which objects should be sent or accepted from which remote connections.

  * If objects are not all synchronized, this can provide another reason why referential integrity may not be maintained.

* Entanglement provides a mechanism where receivers can act on a potential change  before it has been accepted and committed. As an example, if an object is being moved, viewers near the client generating the motion can react to the new position before the object owner has accepted the change.

* Ownership of objects in Entanglement is distributed.  Each object typically has an owner who is ultimately responsible for deciding the state of the object.  However, objects from different owners can be mixed in a collection.
  
