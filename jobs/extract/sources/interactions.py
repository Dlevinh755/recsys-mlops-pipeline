from jobs.extract.source_spec import SourceSpec

SOURCE = SourceSpec(
    name="interactions",
    table="interactions",
    primary_key="interaction_id",
    primary_key_type="bigint",
)
