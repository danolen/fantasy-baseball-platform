{% macro nfbc_parse_number(expr, dtype='double') -%}
cast(
    nullif(
        replace(
            nullif(nullif(trim(cast({{ expr }} as varchar)), ''), '-'),
            ',',
            ''
        ),
        ''
    ) as {{ dtype }}
)
{%- endmacro %}
