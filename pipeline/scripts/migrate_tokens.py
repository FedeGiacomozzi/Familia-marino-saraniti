"""
One-shot migration: populate tokens/{token} index for existing families.

Run once:
    python -m pipeline.scripts.migrate_tokens

Safe to re-run (uses set with merge=False, overwrites are idempotent).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.utils.firestore import _db


def migrate():
    db = _db()
    familias = list(db.collection("familias").stream())
    print(f"Migrando {len(familias)} familias...")

    total = 0
    for familia_doc in familias:
        integrantes = list(
            db.collection("familias").document(familia_doc.id).collection("integrantes").stream()
        )
        for integrante_doc in integrantes:
            data = integrante_doc.to_dict()
            token = data.get("token_unico", "")
            if not token:
                continue
            db.collection("tokens").document(token).set({
                "familia_id": familia_doc.id,
                "integrante_id": integrante_doc.id,
            })
            total += 1
            print(f"  {familia_doc.id}/{integrante_doc.id} → tokens/{token[:8]}...")

    print(f"Migración completa: {total} tokens indexados.")


if __name__ == "__main__":
    migrate()
