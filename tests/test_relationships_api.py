"""End-to-end coverage for the relationships API (Phase 2). Needs the docker
stack (auth_client). Covers type CRUD, edge create/list/delete, the viewpoint
label resolution (a canonical row read from both ends), symmetric
canonicalisation, mutual normalisation, the graph endpoint, and the tenant /
validation guards."""

from __future__ import annotations

import uuid as _uuid

import httpx


def _member(client: httpx.Client, name: str) -> str:
    return client.post("/v1/members", json={"name": name}).json()["id"]


def _group(client: httpx.Client, name: str) -> str:
    return client.post("/v1/groups", json={"name": name}).json()["id"]


def _type(client: httpx.Client, **body) -> str:
    r = client.post("/v1/relationship-types", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- Relationship types ----------------------------------------------------

def test_create_type_symmetric_drops_reverse_label(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/relationship-types",
        json={"name": "Partner", "symmetry": "symmetric",
              "forward_label": "partner", "reverse_label": "ignored"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["reverse_label"] is None


def test_directional_type_requires_reverse_label(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/relationship-types",
        json={"name": "ParentNoRev", "symmetry": "directional",
              "forward_label": "parent"},
    )
    assert r.status_code == 422


def test_duplicate_type_name_conflicts(auth_client: httpx.Client):
    body = {"name": "DupType", "symmetry": "symmetric", "forward_label": "x"}
    assert auth_client.post("/v1/relationship-types", json=body).status_code == 201
    assert auth_client.post("/v1/relationship-types", json=body).status_code == 409


# --- Directional edges + viewpoint resolution ------------------------------

def test_directional_edge_reads_from_both_viewpoints(auth_client: httpx.Client):
    t = _type(auth_client, name="ParentChild", symmetry="directional",
              forward_label="parent", reverse_label="child")
    alice, bob = _member(auth_client, "Pc_Alice"), _member(auth_client, "Pc_Bob")
    r = auth_client.post(
        "/v1/member-relationships",
        json={"source_id": alice, "target_id": bob, "relationship_type_id": t},
    )
    assert r.status_code == 201, r.text

    a_view = auth_client.get(f"/v1/members/{alice}/relationships").json()
    a_edge = next(e for e in a_view if e["relationship_type_id"] == t)
    assert a_edge["label"] == "parent"
    assert a_edge["direction"] == "outgoing"
    assert a_edge["other_id"] == bob

    b_view = auth_client.get(f"/v1/members/{bob}/relationships").json()
    b_edge = next(e for e in b_view if e["relationship_type_id"] == t)
    assert b_edge["label"] == "child"
    assert b_edge["direction"] == "incoming"
    assert b_edge["other_id"] == alice


def test_either_edge_directional_and_mutual(auth_client: httpx.Client):
    t = _type(auth_client, name="Protector", symmetry="either",
              forward_label="protector", reverse_label="protectee")
    a, b = _member(auth_client, "Pr_A"), _member(auth_client, "Pr_B")
    c, d = _member(auth_client, "Pr_C"), _member(auth_client, "Pr_D")
    # Directional: a protects b.
    auth_client.post("/v1/member-relationships",
                     json={"source_id": a, "target_id": b,
                           "relationship_type_id": t, "mutual": False})
    a_edge = next(e for e in auth_client.get(f"/v1/members/{a}/relationships").json()
                  if e["relationship_type_id"] == t)
    assert (a_edge["label"], a_edge["direction"]) == ("protector", "outgoing")
    b_edge = next(e for e in auth_client.get(f"/v1/members/{b}/relationships").json()
                  if e["relationship_type_id"] == t)
    assert (b_edge["label"], b_edge["direction"]) == ("protectee", "incoming")
    # Mutual: c and d protect each other; both read "protector".
    auth_client.post("/v1/member-relationships",
                     json={"source_id": c, "target_id": d,
                           "relationship_type_id": t, "mutual": True})
    for who in (c, d):
        view = auth_client.get(f"/v1/members/{who}/relationships").json()
        e = next(x for x in view if x["relationship_type_id"] == t)
        assert (e["label"], e["direction"], e["mutual"]) == ("protector", "none", True)


def test_mutual_ignored_on_non_either_type(auth_client: httpx.Client):
    t = _type(auth_client, name="PartnerM", symmetry="symmetric",
              forward_label="partner")
    a, b = _member(auth_client, "Pm_A"), _member(auth_client, "Pm_B")
    r = auth_client.post("/v1/member-relationships",
                         json={"source_id": a, "target_id": b,
                               "relationship_type_id": t, "mutual": True})
    assert r.status_code == 201
    assert r.json()["mutual"] is False  # normalised off for symmetric


# --- Symmetric canonicalisation + dedup ------------------------------------

def test_symmetric_inverse_is_duplicate(auth_client: httpx.Client):
    t = _type(auth_client, name="PartnerDup", symmetry="symmetric",
              forward_label="partner")
    a, b = _member(auth_client, "Sd_A"), _member(auth_client, "Sd_B")
    r1 = auth_client.post("/v1/member-relationships",
                          json={"source_id": a, "target_id": b,
                                "relationship_type_id": t})
    assert r1.status_code == 201
    # The inverse of the same pair+type must collide (unordered uniqueness).
    r2 = auth_client.post("/v1/member-relationships",
                          json={"source_id": b, "target_id": a,
                                "relationship_type_id": t})
    assert r2.status_code == 409


# --- Validation guards -----------------------------------------------------

def test_self_edge_rejected(auth_client: httpx.Client):
    t = _type(auth_client, name="SelfT", symmetry="symmetric", forward_label="x")
    a = _member(auth_client, "Self_A")
    r = auth_client.post("/v1/member-relationships",
                         json={"source_id": a, "target_id": a,
                               "relationship_type_id": t})
    assert r.status_code == 400


def test_unknown_type_rejected(auth_client: httpx.Client):
    a, b = _member(auth_client, "Ut_A"), _member(auth_client, "Ut_B")
    r = auth_client.post("/v1/member-relationships",
                         json={"source_id": a, "target_id": b,
                               "relationship_type_id": str(_uuid.uuid4())})
    assert r.status_code == 400


def test_foreign_member_rejected(auth_client: httpx.Client):
    t = _type(auth_client, name="ForeignT", symmetry="symmetric", forward_label="x")
    a = _member(auth_client, "Fm_A")
    r = auth_client.post("/v1/member-relationships",
                         json={"source_id": a, "target_id": str(_uuid.uuid4()),
                               "relationship_type_id": t})
    assert r.status_code == 400


# --- Group edges + graph + delete ------------------------------------------

def test_group_edges_and_graph(auth_client: httpx.Client):
    t = _type(auth_client, name="Allied", symmetry="symmetric", forward_label="allied")
    g1, g2 = _group(auth_client, "Grp1"), _group(auth_client, "Grp2")
    r = auth_client.post("/v1/group-relationships",
                         json={"source_id": g1, "target_id": g2,
                               "relationship_type_id": t})
    assert r.status_code == 201, r.text
    graph = auth_client.get("/v1/relationships/graph?scope=groups").json()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert g1 in node_ids and g2 in node_ids
    assert any(e["relationship_type_id"] == t for e in graph["edges"])
    edge = next(e for e in graph["edges"] if e["relationship_type_id"] == t)
    assert edge["directed"] is False
    assert edge["source_label"] == "allied" and edge["target_label"] == "allied"


def test_delete_edge_and_type(auth_client: httpx.Client):
    t = _type(auth_client, name="DelT", symmetry="symmetric", forward_label="x")
    a, b = _member(auth_client, "Del_A"), _member(auth_client, "Del_B")
    edge_id = auth_client.post(
        "/v1/member-relationships",
        json={"source_id": a, "target_id": b, "relationship_type_id": t},
    ).json()["id"]
    assert auth_client.delete(f"/v1/member-relationships/{edge_id}").status_code == 204
    assert auth_client.get(f"/v1/members/{a}/relationships").json() == [
        e for e in auth_client.get(f"/v1/members/{a}/relationships").json()
        if e["relationship_type_id"] != t
    ]
    # Recreate then delete the TYPE - the edge cascades away.
    auth_client.post("/v1/member-relationships",
                     json={"source_id": a, "target_id": b, "relationship_type_id": t})
    assert auth_client.delete(f"/v1/relationship-types/{t}").status_code == 204
    remaining = auth_client.get(f"/v1/members/{a}/relationships").json()
    assert all(e["relationship_type_id"] != t for e in remaining)


def test_symmetry_immutable_on_update(auth_client: httpx.Client):
    t = _type(auth_client, name="ImmT", symmetry="symmetric", forward_label="x")
    # symmetry is not an accepted update field; name/labels are.
    r = auth_client.patch(f"/v1/relationship-types/{t}",
                          json={"forward_label": "renamed"})
    assert r.status_code == 200
    assert r.json()["forward_label"] == "renamed"
    assert r.json()["symmetry"] == "symmetric"
