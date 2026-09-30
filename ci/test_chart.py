#!/usr/bin/env python3
# Copyright 2022-present Karagatan LLC.
# SPDX-License-Identifier: Apache-2.0
"""
Render-time tests for charts/mailnite, run by CI and by hand:

    HELM=helm KUBECONFORM=kubeconform python3 ci/test_chart.py

HELM may list several binaries (HELM="helm3 helm4"): every scenario renders
with each, since users arrive with either. Each scenario is checked three
ways: kubeconform (strict, Kubernetes + CRD schemas — Gateway API and GKE's
policies included), assertions on what the exposure/platform must and must
not create, and — for bad values — that the install FAILS with a message
that names the fix.
"""
import os
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(ROOT, "charts", "mailnite")
EXAMPLES = os.path.join(CHART, "examples")
HELMS = os.environ.get("HELM", "helm").split()
KUBECONFORM = os.environ.get("KUBECONFORM", "kubeconform")
KUBE_VERSION = os.environ.get("KUBE_VERSION", "1.30.0")
CRD_SCHEMAS = "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
# GKE's CRDs only exist on GKE: tell helm they do, so the platform=gke
# objects render (a real install on GKE sees them for itself).
GKE_APIS = ["networking.gke.io/v1", "cloud.google.com/v1"]

failures = []


def helm_template(helm, values_files=(), sets=(), extra=()):
    cmd = [helm, "template", "mailnite", CHART, "--namespace", "mail"]
    for f in values_files:
        cmd += ["-f", f]
    for s in sets:
        cmd += ["--set", s]
    cmd += list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def docs(out):
    return [d for d in yaml.safe_load_all(out) if d]


def find(objs, kind, name=None):
    return [o for o in objs if o["kind"] == kind and (name is None or o["metadata"]["name"] == name)]


def one(objs, kind, name):
    got = find(objs, kind, name)
    assert len(got) == 1, f"want exactly one {kind}/{name}, got {len(got)}"
    return got[0]


def container(sts, name="mailnite"):
    return next(c for c in sts["spec"]["template"]["spec"]["containers"] if c["name"] == name)


def env(sts):
    return {e["name"]: e for e in container(sts).get("env", [])}


def egress_ports(objs):
    np = one(objs, "NetworkPolicy", "mailnite")
    return {(p["port"], p.get("protocol", "TCP")) for r in np["spec"].get("egress", []) for p in r.get("ports", [])}


def kubeconform(out):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(out)
    p = subprocess.run([KUBECONFORM, "-strict", "-summary", "-kubernetes-version", KUBE_VERSION,
                        "-schema-location", "default", "-schema-location", CRD_SCHEMAS, f.name],
                       capture_output=True, text=True)
    os.unlink(f.name)
    assert p.returncode == 0, "kubeconform:\n" + p.stdout + p.stderr


# ---- per-scenario expectations ------------------------------------------------

def check_defaults(objs):
    assert not find(objs, "Service", "mailnite-mail"), "exposure=none must publish nothing"
    assert not find(objs, "HTTPRoute") and not find(objs, "Ingress")
    sts = one(objs, "StatefulSet", "mailnite")
    spec = sts["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    init = spec["initContainers"][0]
    assert init["args"][:2] == ["k8s", "keys"] and "--secret" in init["args"]
    mounts = {m["name"]: m for m in init["volumeMounts"]}
    assert mounts["kube-api"]["mountPath"] == "/var/run/secrets/kubernetes.io/serviceaccount"
    assert "kube-api" not in {m["name"] for m in container(sts)["volumeMounts"]}, "the server itself gets no token"
    e = env(sts)
    assert e["MAIL_SMK"]["valueFrom"]["secretKeyRef"] == {"name": "mailnite-keys", "key": "MAIL_SMK"}
    assert e["MAIL_PEPPER"]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert sts["spec"]["podManagementPolicy"] == "Parallel"
    assert sts["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "20Gi"
    role = one(objs, "Role", "mailnite-keys")
    assert {"get"} == set(role["rules"][0]["verbs"]) and role["rules"][0]["resourceNames"] == ["mailnite-keys"]
    cj = one(objs, "CronJob", "mailnite-update")
    pod = cj["spec"]["jobTemplate"]["spec"]["template"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/name"] == "mailnite-updater", "updater pods must not match the server's selectors"
    c = pod["spec"]["containers"][0]
    assert c["args"][:2] == ["k8s", "update"] and "--statefulset=mailnite" in c["args"]
    upd = one(objs, "Role", "mailnite-updater")["rules"]
    assert upd == [{"apiGroups": ["apps"], "resources": ["statefulsets"], "resourceNames": ["mailnite"], "verbs": ["get", "patch"]}]
    ports = egress_ports(objs)
    assert (6443, "TCP") in ports, "the key init container must reach a kubeadm API server"
    assert (22, "TCP") not in ports, "SSH egress only with exposure=relay"


def mail_service(objs):
    svc = one(objs, "Service", "mailnite-mail")
    return svc, {p["name"]: p for p in svc["spec"]["ports"]}


def check_lb_common(objs):
    svc, ports = mail_service(objs)
    assert svc["spec"]["type"] == "LoadBalancer"
    assert svc["spec"]["externalTrafficPolicy"] == "Local", "the real client IP must reach the server"
    assert {k: (v["port"], v["targetPort"]) for k, v in ports.items()} == {
        "smtp": (25, 2525), "submissions": (465, 2465), "submission": (587, 2587),
        "imaps": (993, 2993), "imap": (143, 2143), "https": (443, 8443)}
    assert all("nodePort" not in p for p in ports.values())
    assert 80 not in {p["port"] for p in ports.values()}, "plain-HTTP webmail must never be published"
    np_ports = {p["port"] for r in one(objs, "NetworkPolicy", "mailnite")["spec"]["ingress"] for p in r["ports"]}
    assert {8080, 8443, 2525, 2465, 2587, 2993, 2143} <= np_ports
    return svc


def check_gke(objs):
    svc = check_lb_common(objs)
    assert svc["metadata"]["annotations"]["cloud.google.com/l4-rbs"] == "enabled"
    assert svc["spec"]["loadBalancerIP"] == "203.0.113.25"
    assert not find(objs, "GCPBackendPolicy"), "no gateway configured, no gateway policies"


def check_aks(objs):
    svc = check_lb_common(objs)
    a = svc["metadata"]["annotations"]
    assert a["service.beta.kubernetes.io/azure-load-balancer-ipv4"] == "203.0.113.25"
    assert a["service.beta.kubernetes.io/azure-load-balancer-tcp-idle-timeout"] == "30", "Azure's 4-minute idle default cuts IMAP IDLE"
    assert "loadBalancerIP" not in svc["spec"], "AKS takes the IP by annotation"


def check_aks_idle_override(objs):
    svc, _ = mail_service(objs)
    assert svc["metadata"]["annotations"]["service.beta.kubernetes.io/azure-load-balancer-tcp-idle-timeout"] == "60", "the user's annotation wins"


def check_eks(objs):
    svc = check_lb_common(objs)
    a = svc["metadata"]["annotations"]
    assert a["service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"] == "ip"
    assert a["service.beta.kubernetes.io/aws-load-balancer-target-group-attributes"] == "preserve_client_ip.enabled=true"
    assert a["service.beta.kubernetes.io/aws-load-balancer-eip-allocations"].startswith("eipalloc-")
    idle = "service.beta.kubernetes.io/aws-load-balancer-listener-attributes.TCP-"
    for port in (993, 143):
        assert a[f"{idle}{port}"] == "tcp.idle_timeout.seconds=1800", "the NLB's 350 s idle default cuts IMAP IDLE"
    assert f"{idle}25" not in a, "only the IMAP listeners hold idle connections"


def check_eks_imaps_only(objs):
    a = mail_service(objs)[0]["metadata"]["annotations"]
    idle = "service.beta.kubernetes.io/aws-load-balancer-listener-attributes.TCP-"
    assert f"{idle}993" in a and f"{idle}143" not in a, "no listener, no listener attributes"


def routes(objs):
    return {r["metadata"]["name"]: r for r in find(objs, "HTTPRoute")}


def check_cloudflare_relay(objs):
    assert not find(objs, "Service", "mailnite-mail"), "relay: nothing listens publicly"
    r = routes(objs)
    assert set(r) == {"mailnite-mail-path"}, f"relay: mail.<domain> belongs to the VDS, got {set(r)}"
    route = r["mailnite-mail-path"]["spec"]
    assert route["hostnames"] == ["rel2.com"]
    assert route["parentRefs"] == [{"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "external-https", "namespace": "gateway-infra"}]
    assert route["rules"][0]["matches"][0]["path"] == {"type": "PathPrefix", "value": "/mail"}
    assert route["rules"][0]["backendRefs"] == [{"name": "mailnite", "port": 8080}]
    ports = egress_ports(objs)
    assert {(22, "TCP"), (8443, "TCP"), (8443, "UDP")} <= ports, "relay: SSH setup + tunnel egress"


def check_homelab(objs):
    assert not find(objs, "Service", "mailnite-mail") and not find(objs, "HTTPRoute")
    assert (22, "TCP") in egress_ports(objs)


def check_router(objs):
    svc, ports = mail_service(objs)
    assert svc["spec"]["type"] == "NodePort" and svc["spec"]["externalTrafficPolicy"] == "Local"
    assert {k: v["nodePort"] for k, v in ports.items()} == {
        "smtp": 30025, "submissions": 30465, "submission": 30587, "imaps": 30993, "imap": 30143}
    r = routes(objs)
    assert r["mailnite-mail-path"]["spec"]["hostnames"] == ["example.com"]
    host = r["mailnite-host"]["spec"]
    assert host["hostnames"] == ["mail.example.com"]
    assert host["rules"][0]["matches"][0]["path"]["value"] == "/"


def check_gke_gateway(objs):
    check_gke_policies(objs)
    assert set(routes(objs)) == {"mailnite-mail-path"}


def check_gke_policies(objs):
    bp = one(objs, "GCPBackendPolicy", "mailnite")["spec"]
    assert bp["default"]["timeoutSec"] == 3600 and bp["targetRef"]["name"] == "mailnite"
    hc = one(objs, "HealthCheckPolicy", "mailnite")["spec"]["default"]["config"]["httpHealthCheck"]
    assert hc["requestPath"] == "/mail/", "GKE wants a 200; '/' answers 302"


def check_gke_ingress(objs):
    svc = one(objs, "Service", "mailnite")
    assert "cloud.google.com/backend-config" in svc["metadata"]["annotations"]
    bc = one(objs, "BackendConfig", "mailnite")["spec"]
    assert bc["healthCheck"]["requestPath"] == "/mail/" and bc["timeoutSec"] == 3600
    ing = one(objs, "Ingress", "mailnite")["spec"]
    assert ing["rules"][0]["host"] == "example.com" and ing["rules"][0]["http"]["paths"][0]["path"] == "/mail"


def check_existing_secret(objs):
    sts = one(objs, "StatefulSet", "mailnite")
    assert "initContainers" not in sts["spec"]["template"]["spec"], "a brought key needs no minting"
    assert env(sts)["MAIL_SMK"]["valueFrom"]["secretKeyRef"]["name"] == "my-keys"
    assert not find(objs, "Role", "mailnite-keys")
    assert (6443, "TCP") not in egress_ports(objs)


def check_no_keys(objs):
    sts = one(objs, "StatefulSet", "mailnite")
    assert "MAIL_SMK" not in env(sts) and "initContainers" not in sts["spec"]["template"]["spec"]


def check_no_update(objs):
    assert not find(objs, "CronJob") and not find(objs, "Role", "mailnite-updater")


def check_existing_claim(objs):
    sts = one(objs, "StatefulSet", "mailnite")
    assert "volumeClaimTemplates" not in sts["spec"]
    vols = {v["name"]: v for v in sts["spec"]["template"]["spec"]["volumes"]}
    assert vols["data"]["persistentVolumeClaim"]["claimName"] == "mailnite-data"


def check_tag(want):
    def check(objs):
        img = container(one(objs, "StatefulSet", "mailnite"))["image"]
        assert img == f"mailnite/mailnite:{want}", img
    return check


SCENARIOS = [
    # name, values files, --set, extra helm args, check
    ("defaults", [], [], [], check_defaults),
    ("gke", ["gke.yaml"], [], ["--api-versions", GKE_APIS[0], "--api-versions", GKE_APIS[1]], check_gke),
    ("aks", ["aks.yaml"], [], [], check_aks),
    ("aks-idle-override", ["aks.yaml"], [], ["--set-string", "mail.loadBalancer.annotations.service\\.beta\\.kubernetes\\.io/azure-load-balancer-tcp-idle-timeout=60"], check_aks_idle_override),
    ("eks", ["eks.yaml"], [], [], check_eks),
    ("eks-imaps-only", ["eks.yaml"], ["mail.ports.imap.enabled=false"], [], check_eks_imaps_only),
    ("cloudflare-relay", ["cloudflare-relay.yaml"], [], [], check_cloudflare_relay),
    ("homelab-nat", ["homelab-nat.yaml"], [], [], check_homelab),
    ("router-nodeport", ["router-nodeport.yaml"], [], [], check_router),
    ("gke+gateway", ["gke.yaml"], ["gateway.name=external-http", "gateway.namespace=default"], [], check_gke_gateway),
    ("gke+ingress", ["gke.yaml"], ["ingress.enabled=true"], [], check_gke_ingress),
    ("existing-secret", [], ["keys.existingSecret=my-keys"], [], check_existing_secret),
    ("no-keys", [], ["keys.enabled=false"], [], check_no_keys),
    ("no-autoupdate", [], ["autoUpdate.enabled=false"], [], check_no_update),
    ("existing-claim", [], ["persistence.existingClaim=mailnite-data"], [], check_existing_claim),
    ("pinned-tag", [], ["image.tag=1.4.7", "autoUpdate.enabled=false"], [], check_tag("1.4.7")),
]

# Values that must STOP the install, and a phrase the message must carry.
BAD = [
    ("typo'd key", ["exposre=relay"], "exposre"),
    ("unknown exposure", ["exposure=public"], "exposure"),
    ("unknown platform", ["platform=openshift"], "platform"),
    ("two replicas", ["replicaCount=2"], "replicaCount"),
    ("gateway without hosts", ["gateway.name=gw"], "nothing to route"),
    ("ingress without hosts", ["ingress.enabled=true"], "nothing to route"),
    ("EKS with a plain IP", ["platform=eks", "exposure=loadBalancer", "mail.loadBalancer.ip=203.0.113.9"], "eip-allocations"),
    ("upper-case domain", ["domain=Example.com"], "domain"),
    ("every mail port off", ["exposure=nodePort"] + [f"mail.ports.{p}.enabled=false" for p in
                                                     ("smtp", "submissions", "submission", "imaps", "imap", "https")], "every mail.ports entry is disabled"),
]


def run():
    for helm in HELMS:
        for name, files, sets, extra, check in SCENARIOS:
            label = f"[{os.path.basename(helm)}] {name}"
            # the gke+gateway/ingress combos need the GKE APIs announced too
            if name.startswith("gke") and "--api-versions" not in extra:
                extra = extra + ["--api-versions", GKE_APIS[0], "--api-versions", GKE_APIS[1]]
            code, out, err = helm_template(helm, [os.path.join(EXAMPLES, f) for f in files], sets, extra)
            try:
                assert code == 0, f"helm template failed:\n{err}"
                kubeconform(out)
                check(docs(out))
                print(f"ok    {label}")
            except AssertionError as e:
                failures.append(label)
                print(f"FAIL  {label}: {e}")
        for name, sets, phrase in BAD:
            label = f"[{os.path.basename(helm)}] rejects {name}"
            code, _, err = helm_template(helm, (), sets)
            if code != 0 and phrase in err:
                print(f"ok    {label}")
            else:
                failures.append(label)
                print(f"FAIL  {label}: exit {code}, stderr:\n{err}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        sys.exit(1)
    print("\nall chart checks passed")


if __name__ == "__main__":
    run()
