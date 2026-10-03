"""
Project Smart Layer by Azam Puri - CLOUD app (kept separate from the on-prem product in smart_layer/).

The mill's raw ERP never leaves the mill. A small agent at the mill reads the ERP read-only (the ordinary sync),
then pushes only the processed local store and its settings (no passwords) to the cloud over HTTPS. The cloud
hosts a login dashboard, reports and chat per mill; the product code lives only on the cloud server, and access
is turned on or off centrally in the registry.

This folder is self-contained and imports smart_layer as a library. It is NOT part of the installer or the
customer package (tools/make_release.py); it will be incorporated into the main set-up later when needed.
"""
