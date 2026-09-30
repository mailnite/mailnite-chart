{{/*
The NOTES sections that describe the EDGE — how the internet reaches the
server — and the gateway routes. Shared by the install notes (always) and the
upgrade notes (only when this upgrade created them). The context is the dict
NOTES.txt builds: Values, Release, ns, name, domain, ports, shared, dedicated,
viaGateway, webRouted.
*/}}
{{- define "mailnite.notes.edge" -}}
{{- $ns := .ns -}}{{- $name := .name -}}{{- $domain := .domain -}}
{{- if eq .Values.exposure "loadBalancer" }} LoadBalancer

     kubectl -n {{ $ns }} get svc {{ $name }}-mail -w      # wait for EXTERNAL-IP

   DNS (the wizard's DNS step lists the rest — SPF, DKIM, DMARC, MTA-STS):

     mail.{{ $domain }}.   A    <EXTERNAL-IP>{{ with .Values.mail.loadBalancer.ip }}  ({{ . }}){{ end }}
     {{ $domain }}.        MX   10 mail.{{ $domain }}.

   Wizard: on the Mail relay step choose "No relay — reachable directly"
   (this load balancer IS the edge). On the TLS step pick Let's Encrypt and
   accept its Subscriber Agreement: the certificate is validated through this
   load balancer's 443 (TLS-ALPN-01), no port 80 needed — the Servers step
   shows it land ("issued").
{{- if not .Values.mail.loadBalancer.ip }}
   ! No static IP: the address changes if the Service is ever recreated, and
     MX, PTR and reputation hang off it. Reserve one: mail.loadBalancer.ip.
{{- end }}
{{- if has .Values.platform (list "gke" "aks" "eks") }}

   OUTBOUND MAIL: port 25 egress may be blocked on your {{ get (dict "gke" "Google Cloud" "aks" "Azure" "eks" "AWS") .Values.platform }} account —
   free and pay-as-you-go accounts usually are, corporate ones often not
   ({{ get (dict "gke" "Google exempts some established projects" "aks" "Enterprise Agreement and MCA-E subscriptions may send" "eks" "AWS lifts its limit on request") .Values.platform }}).
   Test it in Infrastructure → Outbound; if it is blocked, set a smarthost
   there (a relay service on 587/465 — SES, SendGrid, Mailgun, Postmark …).
   Receiving is unaffected.
{{- end }}
{{- else if eq .Values.exposure "nodePort" }} NodePort (router forwarding)

   Forward these public ports on the router/firewall to any node:
{{- range .ports }}
     {{ printf "%-5v" .port }} → node:{{ .nodePort }}   ({{ .name }})
{{- end }}

   DNS: mail.{{ $domain }} A <the router's public IP>; {{ $domain }} MX 10 mail.{{ $domain }}.
   Ask the ISP for a PTR record: that IP → mail.{{ $domain }}.

   Wizard: on the Mail relay step choose "No relay — reachable directly".
{{- if not .Values.mail.ports.https.enabled }}
   Your gateway answers mail.{{ $domain }}:443, so Let's Encrypt validates over
   HTTP-01 through it: http://mail.{{ $domain }}/.well-known/acme-challenge/
   must reach the gateway (a redirect to https is fine).
{{- end }}
{{- else if eq .Values.exposure "relay" }} through a mailrelay

   Nothing here listens publicly: the server dials OUT to a mailrelay on a
   small VDS that owns the public IP (it stores no mail and no keys).

   • Wizard → Mail relay → "Relay: automatic setup": give it the VDS's SSH
     login. It installs the relay, pairs it (mTLS) and keeps it auto-updated.
   • DNS: mail.{{ $domain }} A <the VDS's IP>, MX 10 mail.{{ $domain }}. On Cloudflare make
     that record "DNS only" — the proxy carries no SMTP/IMAP.
   • Ports step: choose Relay for BOTH groups. Emailed links (password resets
     …) point at https://mail.{{ $domain }}/mail, which only the relay can serve.
{{- if .webRouted }}
     The gateway routes below keep working alongside: in a pod the server
     answers the tunnel and the cluster at once.
{{- else }}
     With no gateway set, the relay is also how browsers reach webmail.
{{- end }}
   • Set the VDS's reverse DNS (PTR) to mail.{{ $domain }}: outbound mail leaves
     from its IP.
{{- else }} not yet (exposure=none)

   Nothing is exposed. When the wizard is done, pick how the internet reaches
   this cluster and upgrade:

     helm upgrade {{ .Release.Name }} <chart> -n {{ $ns }} --reuse-values --set exposure=<mode>

       loadBalancer   its own public IP (GKE, AKS, EKS, MetalLB, k3s)
       relay          behind Cloudflare or a NAT — through a mailrelay VDS
       nodePort       a router forwards the ports to fixed node ports
{{- end }}
{{- end }}

{{- define "mailnite.notes.gateway" }}
4) Webmail through Gateway {{ .Values.gateway.namespace | default .ns }}/{{ .Values.gateway.name }}:
{{- range .shared }}
     https://{{ . }}/mail
{{- end }}
{{- range .dedicated }}
     https://{{ . }}/
{{- end }}

   Behind a gateway, tell the server whom to trust for the client address:
   Admin → Security → Reverse proxy (the gateway's addresses; provider
   Cloudflare when it sits in front). Otherwise rate limits and the login log
   see ONE client — the gateway.
   Cloudflare: rules that add Content-Security-Policy must skip /mail/.
{{- end }}
