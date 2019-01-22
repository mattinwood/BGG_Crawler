#%%
from seleniumrequests import Firefox
from selenium.common.exceptions import NoSuchElementException
from time import sleep
from os import getenv
from sqlalchemy import create_engine, engine
import pandas as pd
import pickle
import re
import yaml


AURORA_HOST = 'inwood-analytics.cnnfhkgooetn.us-west-2.rds.amazonaws.com'
AURORA_DB = 'analytics'
AURORA_PORT = '3306'
AURORA_USER = getenv('AURORA_USER')
AURORA_PASS = getenv('AURORA_PASS')


def engine_builder():
    db_connect_url = engine.url.URL(
        drivername='mysql',
        username=AURORA_USER,
        password=AURORA_PASS,
        host=AURORA_HOST,
        port=AURORA_PORT,
        database=AURORA_DB)
    return create_engine(db_connect_url)


#%%
class StoneAge:
    def __init__(self, browser):
        self.browser = browser
        self.browser.get('http://en.boardgamearena.com')

        try:
            self.read_unparsed_game_ids()
        except FileNotFoundError:
            self.game_ids = set()
            open('data/new_games.yaml', 'a').close()

    def login(self):
        if 'boardgamearena.com' not in self.browser.current_url:
            self.browser.get('http://en.boardgamearena.com')

        try:
            if not self.browser.find_element_by_id('login-status').text:
                logged_in = False
            else:
                logged_in = True
        except NoSuchElementException:
                logged_in = False

        if not logged_in:
            login_url = 'http://en.boardgamearena.com/#!account?redirect=headlines'
            self.browser.get(login_url)

            self.browser.find_element_by_id("username_input").clear()
            username = self.browser.find_element_by_id("username_input")
            username.send_keys(getenv('BGG_USER'))

            self.browser.find_element_by_id("password_input").clear()
            password = self.browser.find_element_by_id("password_input")
            password.send_keys(getenv('BGG_PASS'))

            self.browser.find_element_by_id("login_button").click()
        else:
            print("Already logged in")
    
    def get_recent_game_ids(self):
        url = 'http://en.boardgamearena.com/#!gamepanel?game=stoneage&section=lastresults'
        self.browser.get(url)

        for game in self.browser.find_elements_by_class_name('gamename'):
            self.game_ids.add(game.find_element_by_xpath('..').get_property('href')[44:])

    def write_new_game_ids(self):
        with open('data/new_games.yaml', 'w') as outfile:
            yaml.dump(self.game_ids, outfile)

    def read_unparsed_game_ids(self):
        with open("data/new_games.yaml", 'r') as stream:
            self.game_ids = yaml.load(stream)

    def game_results(self, url):
        loaded = False
        self.browser.get(url)
        while not loaded:
            try:
                self.browser.find_element_by_id('player_stats_table')
                loaded = True
            except NoSuchElementException:
                sleep(0.5)

        table = self.browser.find_element_by_id('player_stats_table')
        results = {}

        rows = table.find_elements_by_tag_name('tr')

        for row in rows:
            row_name = row.find_element_by_tag_name('th').text
            if row_name == '':
                row_name = 'Player Names'
                values = [x.text for x in row.find_elements_by_tag_name('th')]
                values.pop(0)
            else:
                values = [x.text for x in row.find_elements_by_tag_name('td')]
            if row_name != 'All stats':
                results[row_name] = values

        panel = self.browser.find_element_by_id('game_result_panel')

        newranks = [x.text for x in panel.find_elements_by_class_name('gamerank_value')]
        results['new_rank'] = newranks

        winpoints = [x.text for x in panel.find_elements_by_class_name('winpoints')]
        results['winpoints'] = winpoints

        return results

    def game_logs(self, url):
        loaded = False
        self.browser.get(url)
        while not loaded:
            try:
                self.browser.find_element_by_id('gamelogs')
                loaded = True
            except NoSuchElementException:
                sleep(0.5)

        game = self.browser.find_element_by_id('gamelogs')
        actions = game.find_elements_by_class_name('gamelogreview')
        actions = [x.text for x in actions]

        return actions

    def game_info(self, game_id):
        results_url = 'https://en.boardgamearena.com/#!table?table={0}'
        replay_url = 'http://en.boardgamearena.com/#!gamereview?table={0}'

        summary_results = self.game_results(results_url.format(game_id))
        pickle.dump(summary_results, open('data/results.pkl', 'wb'))

        logs = self.game_logs(replay_url.format(game_id))
        pickle.dump(logs, open('data/logs.pkl', 'wb'))

        # Log Cleanup
        player_order = list(set([x[0:x.find(' is')] for x in logs if 'is now first player' in x][0:4]))

        player_nums = []
        for x in logs:
            if 'end' in x:
                player_nums.append(-1)
            else:
                for i in range(len(player_order)):
                    if x.find(player_order[i]) >= 0:
                        player_nums.append(i)

        for player in player_order:
            logs = [x.replace(player, 'player') for x in logs]

        values = [int((re.findall('\d+', x) or [-1])[0]) for x in logs]

        logs = [re.sub(r'\d', 'i', x) for x in logs]

        log_df = pd.DataFrame({
            'player_number': player_nums,
            'value': values,
            'action_name': logs,
        })

        log_df.loc[log_df['action_name'] == 'player is now first player', 'new_turn'] = 1
        log_df['turn_number'] = log_df['new_turn'].fillna(0).cumsum()
        log_df['move_number'] = log_df.index + 1
        log_df['game_id'] = game_id
        log_df = log_df.drop('new_turn', axis=1)

        summary_df = pd.DataFrame(summary_results)
        summary_df.columns = summary_df.columns.str.replace("'", "").str.lower().str.replace(' ', '_')
        summary_df['game_id'] = game_id

        log_df.to_sql('game_logs', engine_builder(), schema='bgg', if_exists='append', index=False)
        summary_df.to_sql('game_summary', engine_builder(), schema='bgg', if_exists='append', index=False)

        self.game_ids.remove(game_id)


#%%
if __name__ == '__main__':
    b = StoneAge(Firefox())
    b.get_recent_game_ids()
    b.login()

    working_list = b.game_ids
    for g_id in working_list:
        b.game_info(g_id)
        sleep(2)
    b.browser.close()
    b.write_new_game_ids()
