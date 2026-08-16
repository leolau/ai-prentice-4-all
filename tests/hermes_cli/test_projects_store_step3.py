"""Store-level contracts for the step-3 additions to ``projects_db``:
outputs, deliveries, contacts, link kinds and the generic record patcher.

The API-level behaviour lives in test_projects_api.py; here we pin what the
store itself must refuse — the CLI path hits these helpers directly, so the
guarantees cannot live only in the router (design §16).
"""

from __future__ import annotations

import pytest

from hermes_cli import projects_db as pdb


@pytest.fixture
def conn(tmp_path):
    c = pdb.connect(db_path=tmp_path / "projects.db")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def pid(conn):
    return pdb.create_full_project(
        conn, goal="Ship the digest", description="Weekly digest."
    )


def test_outputs_declare_in_order_and_deliveries_accumulate(conn, pid):
    o1 = pdb.add_project_output(conn, project_id=pid, title="The digest email")
    o2 = pdb.add_project_output(
        conn, project_id=pid, title="The changelog note", required=False
    )
    rows = pdb.get_project_outputs(conn, pid)
    assert [(r["title"], r["seq"]) for r in rows] == [
        ("The digest email", 1),
        ("The changelog note", 2),
    ]

    d1 = pdb.record_output_delivery(conn, output_id=o1, label="Mon 1")
    d2 = pdb.record_output_delivery(conn, output_id=o1, label="Mon 2")
    assert d1 != d2
    by_output = pdb.get_output_deliveries(conn, output_id=o1)
    assert {d["label"] for d in by_output} == {"Mon 1", "Mon 2"}
    by_project = pdb.get_output_deliveries(conn, project_id=pid)
    assert len(by_project) == 2

    with pytest.raises(ValueError):
        pdb.record_output_delivery(conn, output_id="o_missing")


def test_delete_last_required_output_refused_optional_not(conn, pid):
    oid = pdb.add_project_output(conn, project_id=pid, title="Required one")
    with pytest.raises(ValueError, match="last required output"):
        pdb.remove_project_output(conn, oid)

    optional = pdb.add_project_output(
        conn, project_id=pid, title="Optional", required=False
    )
    assert pdb.remove_project_output(conn, optional) is True

    # A second required output unblocks the deletion.
    pdb.add_project_output(conn, project_id=pid, title="Required two")
    assert pdb.remove_project_output(conn, oid) is True


def test_accept_is_a_human_stamped_transition(conn, pid):
    oid = pdb.add_project_output(conn, project_id=pid, title="The digest")
    assert pdb.accept_project_output(conn, oid, accepted_by="ada") is True
    row = pdb.get_project_outputs(conn, pid)[0]
    assert row["status"] == "accepted"
    assert row["accepted_by"] == "ada"
    assert row["accepted_at"]

    # update_project_output refuses to set accepted: only the accept route.
    with pytest.raises(ValueError, match="accept"):
        pdb.update_project_output(conn, oid, status="accepted")


def test_output_status_delivered_stamps_delivered_at(conn, pid):
    oid = pdb.add_project_output(conn, project_id=pid, title="The digest")
    assert pdb.update_project_output(conn, oid, status="delivered") is True
    row = pdb.get_project_outputs(conn, pid)[0]
    assert row["status"] == "delivered"
    assert row["delivered_at"]

    with pytest.raises(ValueError):
        pdb.update_project_output(conn, oid, status="invented")


def test_link_kinds_are_validated(conn, pid):
    assert pdb.add_project_link(
        conn, project_id=pid, kind="sample", profile="default", ref="/a.md"
    )
    assert pdb.add_project_link(
        conn, project_id=pid, kind="memory", profile="default", ref="m_1"
    )
    with pytest.raises(ValueError, match="link kind"):
        pdb.add_project_link(
            conn, project_id=pid, kind="shortcut", profile="default", ref="/x"
        )
    assert pdb.remove_project_link(
        conn, project_id=pid, kind="sample", profile="default", ref="/a.md"
    )
    assert not pdb.remove_project_link(
        conn, project_id=pid, kind="sample", profile="default", ref="/a.md"
    )


def test_contacts_crud_and_field_validation(conn, pid):
    cid = pdb.add_project_contact(
        conn,
        project_id=pid,
        name="The client",
        platform="email",
        address="client@example.com",
    )
    rows = pdb.get_project_contacts(conn, pid)
    assert rows[0]["address"] == "client@example.com"

    assert pdb.update_project_contact(conn, cid, org="ACME", address=None)
    assert pdb.get_project_contacts(conn, pid)[0]["org"] == "ACME"

    with pytest.raises(ValueError, match="unknown contact field"):
        pdb.update_project_contact(conn, cid, password="hunter2")
    with pytest.raises(ValueError, match="name"):
        pdb.update_project_contact(conn, cid, name="  ")
    with pytest.raises(ValueError, match="name"):
        pdb.add_project_contact(conn, project_id=pid, name=" ")

    assert pdb.remove_project_contact(conn, cid) is True
    assert pdb.remove_project_contact(conn, cid) is False


def test_update_project_fields_validates_and_rejects_status(conn, pid):
    assert pdb.update_project_fields(
        conn,
        pid,
        {
            "cadence": "standing",
            "review_every": "30d",
            "max_in_progress": 2,
            "budget_usd_per_run": 1.5,
            "visibility": "private",
        },
    )
    proj = pdb.get_project(conn, pid)
    assert proj.cadence == "standing"
    assert proj.max_in_progress == 2
    assert proj.visibility == "private"

    with pytest.raises(ValueError, match="set_project_status"):
        pdb.update_project_fields(conn, pid, {"status": "active"})
    with pytest.raises(ValueError, match="unknown project field"):
        pdb.update_project_fields(conn, pid, {"invented": 1})
    with pytest.raises(ValueError, match="cadence"):
        pdb.update_project_fields(conn, pid, {"cadence": "weekly"})
    with pytest.raises(ValueError, match="max_in_progress"):
        pdb.update_project_fields(conn, pid, {"max_in_progress": 0})
