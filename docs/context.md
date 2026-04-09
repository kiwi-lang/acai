AI Agent Swarm
==============

This repository defines an AI agent orchestrator/scheduler geared toward coding.

The user will open a project and converse with an agent about the project (Goals, issues, bugs, new feature).

Through the conversation the agent will generate documents and schedule work to be done.

As the main conversation get bigger to keep the conversation focussed the scribe agent will summarize the conversation
removing question/replies and reformulating as a clear specification.

As work get scheduled the curator agent will do a first pass and build a context for the work agent.
The curator looks at spec doc to attach to the work context, additionally it will add the MCP tools needed for the work.

If the work agent is self hosted it will pop the orchestrator queue and push result to it.
If the work agent is an API, an API worker can be spawn and it will do the same.

NOTE: all the LLM steps are actually performed by the work agent, they only have different
system prompts, MCP capabilities, and the type of output is different.

The different steps/workitem are queued into a SQLite database.
The orchestrator is a simple REST app with an UI to converse with the LLM and inspect the work queue.


Configuration
-------------

We expect to have many toggles and dials they could be located to config.py


Projects
--------

Projects are defined as a git repository.

It will have a yaml file setting the git repo, and common tools (venv, directory layout etc...)

Work agent will branch from the main work tree and commit their work.

The scribe will maintain the documentation to always be up to date.

The curator will browse the documentation and include the relevant bits to the work item.

The work planner will schedule work into multiple steps (default flow: Interface, testing, implementation, document)

The work agent will pick up each planned work and do it 

The project documentation will be used as system prompt.


Testing Logic
^^^^^^^^^^^^^

AI agent for coding are unreliable, to paliate to this issue we will enforce testing for all the features.
The testing schema will be that for every source files `src/<path>/<source>.py` there will be a `tests/<path>/test_<source>.py` that will
be ran to make sure everything is working and nothing breaks.

In addition the testing will be implemented first and the implementation second.

Processes
---------

Orchestrator
^^^^^^^^^^^^

The orchestrator is a simple REST API with a react UI for human interaction (Talk to the AI to plan, configure projects, inspect worker status and the work queue etc...)
To limit database size all the files will be saved as path to include.

When work is poped from the queue, the orchestrator will preprocess the work item
into the conversation context the LLM expects.
The preprocess step should be standalone as we might want to enable the worker to do it.


Worker
^^^^^^

The worker is a REST API server.
It will periodically pop work from the orchestrator and send the work to a local LLM or an API.

For work item that modifies the project files it will setup a github work branch and commit at the end.
It can also run automated testing to make sure the agent finished.

The worker will be in charge of calling/applying the tools the agent wants to run (i.e the Worker is a MCP server ish).


Agents
------

Conversation
^^^^^^^^^^^^

* Context
    * Project Settings
    * Project Summary

* Capabilities
    * Schedule Curator


Scribe
^^^^^^

* Context
    * Project Settings
    * Project Summary

* Capabilities
    * Update documentation
    * Clean up conversation


Curator
^^^^^^^

To limit output token gen this output filenames to be included.

* Context
    * Project Settings
    * Spec Files

* Output:
    * list of filenames to include


WorkItem
^^^^^^^^

* Context
    * Project Settings
    * Spec Files
    * Doc Files
    * Steps

* Output
    Commit
