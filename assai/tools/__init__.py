



def namespaced_route(app, namespace):
    if not namespace.startswith("/"):
        namespace = "/" + namespace

    # Handle both ASSAI instance and Flask app
    flask_app = app.app if hasattr(app, 'app') else app

    def route(url_pat, *args, **kwargs):
        if not url_pat.startswith("/"):
            url_pat = "/" + url_pat
        return flask_app.route(namespace + url_pat, *args, **kwargs)
    return route