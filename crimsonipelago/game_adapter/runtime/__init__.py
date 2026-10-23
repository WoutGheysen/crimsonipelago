"""Runtime (in-process) integration: the Crimsonipelago game plugin and the client's link to it.

The plugin (plugin/, loaded into Crimson Desert by an ASI loader) listens on 127.0.0.1:38301 and speaks
newline-delimited JSON. R0 only proves the link: hello, ping and game facts. Live item delivery and live
check detection build on it (docs/research/realtime.md).
"""
