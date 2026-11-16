# Agent harness

Starter, Document Intelligence, and SRE share a trusted runtime for the Agents
SDK loop, conversation state, and tool calls that need credentials. Each
conversation runs its sandbox tools in an Agent Substrate Actor.

For the turn and recovery contracts, component map, and extension points,
start with the harness's component guide. [Shared harness guide](../harness/README.md)

To integrate the provider into your own SDK application, use the provider
guide. [SDK integration](usage.md#integrate-into-your-application) · [Architecture](architecture.md)
