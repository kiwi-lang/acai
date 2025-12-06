



def namespaced_route(app, namespace):
    if not namespace.startswith("/"):
        namespace = "/" + namespace
    
    def route(url_pat, *args, **kwargs):
        if not url_pat.startswith("/"):
            url_pat = "/" + url_pat
        return app.route(namespace + url_pat, *args, **kwargs)
    return route