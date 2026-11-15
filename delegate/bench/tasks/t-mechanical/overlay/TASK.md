# Task: ingress-and-authz

## Goal
Add two Kubernetes manifests for the Istio ambient Online Boutique demo: a kgateway ingress (Gateway plus HTTPRoute) and an Istio AuthorizationPolicy that lets only the frontend call checkoutservice.

## Read
k8s-demo/demo-frontend.yaml
bench_checks/check_ingress_authz.py

## May change
k8s-istio-demo/ingress.yaml
k8s-istio-demo/authz.yaml

## Constraints
- `k8s-istio-demo/ingress.yaml` holds two documents separated by `---`: a Gateway, then an HTTPRoute. Both are in namespace `boutique`.
  - Gateway: apiVersion `gateway.networking.k8s.io/v1`, name `boutique-gateway`, gatewayClassName `kgateway`, one listener named `http`, port 80, protocol HTTP, allowedRoutes namespaces from `Same`.
  - HTTPRoute: apiVersion `gateway.networking.k8s.io/v1`, name `frontend`, parentRef to `boutique-gateway`, one rule matching path prefix `/`, backendRef Service `frontend` port 80.
- `k8s-istio-demo/authz.yaml` holds one AuthorizationPolicy: apiVersion `security.istio.io/v1`, name `checkout-from-frontend-only`, namespace `boutique`, `targetRefs` (not `selector`) with group `""`, kind `Service`, name `checkoutservice`, action ALLOW, one rule allowing source principal `cluster.local/ns/boutique/sa/frontend`.
- Plain YAML, no comments needed. Do not edit the check script.

## Done when
/usr/bin/python3 bench_checks/check_ingress_authz.py
