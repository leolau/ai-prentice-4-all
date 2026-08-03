# OS patching and service restarts

`unattended-upgrades` patches Ubuntu packages nightly. That is wanted — it is
the only patching mechanism for the ~40 OS packages Hermes depends on (libc,
ca-certificates, sqlite, tzdata). What is not wanted is its side effect.

## The problem

When apt patches a library a Hermes service links against, `needrestart`
restarts that unit immediately, at whatever hour apt happened to run. On
`hermes-systest` on 2026-08-01 at 06:42 it bounced `hermes-gateway` and
`hermes-dashboard`, killing the agent turn that was in flight mid-reply.

## The exemption

```perl
# /etc/needrestart/conf.d/50-hermes.conf
$nrconf{override_rc}{qr(^hermes-)} = 0;
```

`0` means *report but do not restart*. Verify with `perl -c` before leaving it
in place — a syntax error in this directory breaks every subsequent apt run.

**Assign into the hash; do not replace it.** Writing
`$nrconf{override_rc} = { qr(^hermes-) => 0 }` discards the 43 distro defaults
that stop apt restarting `dbus`, `docker`, `getty`, `systemd-logind` and the
rest out from under a live session — trading one outage for a worse one. The
files load in order, and the last assignment wins.

Check the *effective* config rather than the file you wrote:

```bash
perl -e 'my %nrconf;
for my $f ("/etc/needrestart/needrestart.conf",
           "/etc/needrestart/conf.d/50-hermes.conf") {
    my $src = do { local (@ARGV, $/) = $f; <> }; eval $src; die $@ if $@;
}
print scalar(keys %{$nrconf{override_rc}}), " entries\n";'
# expect ~44 entries, not 1
```

## The trade

Patched libraries land on disk, but running Hermes processes keep the old ones
mapped until the unit is restarted. No surprise outage — and no fix in effect
either, until a deliberate restart:

```bash
systemctl restart 'hermes-*.service'
```

So the exemption converts an unpredictable outage into a **restart you owe**.
Nothing currently tracks that debt; `needrestart -b -r l` lists units running
stale libraries, which is the signal a monitor would consume.
