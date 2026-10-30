from ai_review.outcomes import admit_outcomes


def admit_batches(*, config, manifest, finding_batches, critique_batches):
    admit_outcomes(
        ((b["reviewer"], b) for b in finding_batches),
        stage="review",
        config=config,
        manifest=manifest,
    )
    admit_outcomes(critique_batches, stage="critique", config=config, manifest=manifest)
