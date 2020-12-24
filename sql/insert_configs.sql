-- :name insert_configs :affected
insert into bgg.game_configs (
    game_id,
    game_mode,
    gamespeed,
    gamespeed_desc,
    harsh_winter,
    wild_animals,
    igloos,
    new_huts,
    mammoth_herd
)
values (
    :game_id,
    :game_mode,
    :gamespeed,
    :gamespeed_desc,
    :harsh_winter,
    :wild_animals,
    :igloos,
    :new_huts,
    :mammoth_herd);