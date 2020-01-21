-- :name insert_logs :affected
insert into bgg.game_logs (
  player_number,
  value,
  action_name,
  turn_number,
  move_number,
  game_id)
values (
  :player_number,
  :value,
  :action_name,
  :turn_number,
  :move_number,
  :game_id);