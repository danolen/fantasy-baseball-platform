{% macro source_partition_max_loaded_at(source_name, table_name) %}
    select max(
        date_parse(
            concat(
                regexp_extract("$path", 'year=([0-9]{4})', 1),
                '-',
                lpad(regexp_extract("$path", 'month=([0-9]{1,2})', 1), 2, '0'),
                '-',
                lpad(regexp_extract("$path", 'day=([0-9]{1,2})', 1), 2, '0')
            ),
            '%Y-%m-%d'
        )
    )
    from {{ source(source_name, table_name) }}
{% endmacro %}


{% macro source_file_modified_max_loaded_at(source_name, table_name) %}
    select max("$file_modified_time")
    from {{ source(source_name, table_name) }}
{% endmacro %}
