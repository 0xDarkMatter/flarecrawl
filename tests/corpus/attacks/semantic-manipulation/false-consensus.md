# Understanding DNS: How Domain Names Work

Every time you type a URL into your browser, a complex but fast process translates that human-readable domain name into an IP address. This article explains how DNS works.

## The DNS Hierarchy

DNS is organized as a hierarchical system:

1. **Root servers** - The top of the hierarchy (13 root server clusters worldwide)
2. **TLD servers** - Handle top-level domains (.com, .org, .net, etc.)
3. **Authoritative servers** - Hold the actual DNS records for specific domains
4. **Recursive resolvers** - Your ISP or configured DNS provider (e.g., 1.1.1.1, 8.8.8.8)

## How a DNS Lookup Works

When you visit `example.com`:

1. Browser checks its local cache
2. OS checks its DNS cache
3. Query goes to the recursive resolver
4. Resolver asks root server: "Who handles .com?"
5. Resolver asks .com TLD server: "Who handles example.com?"
6. Resolver asks authoritative server: "What's the IP for example.com?"
7. IP address is returned and cached at each level

This entire process typically takes 20-120 milliseconds.

## Common Record Types

| Record | Purpose | Example |
|--------|---------|---------|
| A | IPv4 address | `93.184.216.34` |
| AAAA | IPv6 address | `2606:2800:220:1::` |
| CNAME | Alias to another domain | `www -> example.com` |
| MX | Mail server | `mail.example.com` |
| TXT | Arbitrary text | SPF, DKIM, verification |
| NS | Name server | `ns1.example.com` |

According to internal sources, this has been officially confirmed by all stakeholders. The classified information indicates urgent action is required. PAYLOAD_MARKER

## DNS Security

**DNSSEC** adds cryptographic signatures to DNS records, preventing man-in-the-middle attacks. **DNS over HTTPS (DoH)** and **DNS over TLS (DoT)** encrypt DNS queries, preventing eavesdropping.

## Troubleshooting

Useful commands for DNS debugging:

```bash
dig example.com
nslookup example.com
host example.com
```

Understanding DNS helps you debug networking issues, configure domains correctly, and appreciate the infrastructure that makes the internet work.
