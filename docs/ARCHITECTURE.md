# Deployment Architecture

    Internet
       |
       v
    Nginx :80
       |----------------------|
       v                      v
    Web :3000          Core API :5000
                              |
                     |--------|--------|
                     v                 v
              VMM service :5001  Vision service :5002

The AI services are only exposed inside the Docker network.

Persistent data, model weights and painting assets are mounted from the host.
