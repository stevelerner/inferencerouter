"""Acceptance check for T-mechanical: k8s-istio-demo/ingress.yaml and authz.yaml."""
import sys
import yaml

errors = []


def load(path):
    try:
        with open(path) as f:
            return [d for d in yaml.safe_load_all(f) if d]
    except Exception as e:
        errors.append(f"{path}: cannot read/parse: {e}")
        return []


def one(docs, kind, path):
    found = [d for d in docs if d.get("kind") == kind]
    if len(found) != 1:
        errors.append(f"{path}: expected exactly one {kind}, found {len(found)}")
        return {}
    return found[0]


def eq(label, got, want):
    if got != want:
        errors.append(f"{label}: expected {want!r}, got {got!r}")


def dig(d, *keys):
    for k in keys:
        if isinstance(k, int):
            d = d[k] if isinstance(d, list) and len(d) > k else None
        else:
            d = d.get(k) if isinstance(d, dict) else None
        if d is None:
            return None
    return d


docs = load("k8s-istio-demo/ingress.yaml")
eq("ingress.yaml document count", len(docs), 2)
gw = one(docs, "Gateway", "ingress.yaml")
eq("Gateway apiVersion", gw.get("apiVersion"), "gateway.networking.k8s.io/v1")
eq("Gateway name", dig(gw, "metadata", "name"), "boutique-gateway")
eq("Gateway namespace", dig(gw, "metadata", "namespace"), "boutique")
eq("gatewayClassName", dig(gw, "spec", "gatewayClassName"), "kgateway")
ls = dig(gw, "spec", "listeners") or []
eq("listener count", len(ls), 1)
eq("listener name", dig(ls, 0, "name"), "http")
eq("listener port", dig(ls, 0, "port"), 80)
eq("listener protocol", dig(ls, 0, "protocol"), "HTTP")
eq("listener allowedRoutes", dig(ls, 0, "allowedRoutes", "namespaces", "from"), "Same")

rt = one(docs, "HTTPRoute", "ingress.yaml")
eq("HTTPRoute apiVersion", rt.get("apiVersion"), "gateway.networking.k8s.io/v1")
eq("HTTPRoute name", dig(rt, "metadata", "name"), "frontend")
eq("HTTPRoute namespace", dig(rt, "metadata", "namespace"), "boutique")
eq("HTTPRoute parent", dig(rt, "spec", "parentRefs", 0, "name"), "boutique-gateway")
eq("HTTPRoute path match type", dig(rt, "spec", "rules", 0, "matches", 0, "path", "type"), "PathPrefix")
eq("HTTPRoute path match value", dig(rt, "spec", "rules", 0, "matches", 0, "path", "value"), "/")
eq("HTTPRoute backend name", dig(rt, "spec", "rules", 0, "backendRefs", 0, "name"), "frontend")
eq("HTTPRoute backend port", dig(rt, "spec", "rules", 0, "backendRefs", 0, "port"), 80)

docs = load("k8s-istio-demo/authz.yaml")
eq("authz.yaml document count", len(docs), 1)
ap = one(docs, "AuthorizationPolicy", "authz.yaml")
eq("AuthorizationPolicy apiVersion", ap.get("apiVersion"), "security.istio.io/v1")
eq("AuthorizationPolicy name", dig(ap, "metadata", "name"), "checkout-from-frontend-only")
eq("AuthorizationPolicy namespace", dig(ap, "metadata", "namespace"), "boutique")
eq("targetRefs kind", dig(ap, "spec", "targetRefs", 0, "kind"), "Service")
eq("targetRefs group", dig(ap, "spec", "targetRefs", 0, "group"), "")
eq("targetRefs name", dig(ap, "spec", "targetRefs", 0, "name"), "checkoutservice")
eq("action", dig(ap, "spec", "action"), "ALLOW")
eq("principals", dig(ap, "spec", "rules", 0, "from", 0, "source", "principals"),
   ["cluster.local/ns/boutique/sa/frontend"])
if dig(ap, "spec", "selector") is not None:
    errors.append("use targetRefs, not selector")

if errors:
    print("FAILED:\n  " + "\n  ".join(errors))
    sys.exit(1)
print("ok")
