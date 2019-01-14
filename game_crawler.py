#%%
from seleniumrequests import Firefox
from selenium.common.exceptions import NoSuchElementException
from time import sleep
from os import getenv
import pandas as pd
import pickle
import re
import yaml


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
        player_order = [x[0:x.find(' is')] for x in logs if 'is now first player' in x][0:3]

        player_order.insert(0, 'end') # Covers the end of the game flag

        player_nums = [player_order.index(set(x.split()).intersection(set(player_order)).pop()) for x in logs][:]
        player_order.pop(0)

        for player in player_order:
            logs = [x.replace(player, 'player') for x in logs]

        values = [int((re.findall('\d+', x) or [-1])[0]) for x in logs]

        logs = [re.sub(r'\d', 'i', x) for x in logs]

        df = pd.DataFrame({
            'player_number': player_nums,
            'value': values,
            'action_name': logs,
        })

        df.loc[df['action_name'] == 'player is now first player', 'new_turn'] = 1
        df['turn_number'] = df['new_turn'].fillna(0).cumsum()
        df['move_number'] = df.index + 1
        df['game_id'] = game_id

        print(df.head())

#%%
b = StoneAge(Firefox())
b.login()


#%%
# b.get_recent_game_ids()
b.game_info(47528560)


#%% debug only
# summary_results = pickle.load(open('data/results.pkl', 'rb'))
# logs = pickle.load(open('data/logs.pkl', 'rb'))
#
# player_order = [x[0:x.find(' is')] for x in logs if 'is now first player' in x][0:3]
#
# player_order.insert(0, 'end') # Covers the end of the game flag
#
# player_nums = [player_order.index(set(x.split()).intersection(set(player_order)).pop()) for x in logs][:]
# player_order.pop(0)
#
# for player in player_order:
#     logs = [x.replace(player, 'player') for x in logs]
#
# values = [int((re.findall('\d+', x) or [-1])[0]) for x in logs]
#
# logs = [re.sub(r'\d+', 'i', x) for x in logs]
#
# df = pd.DataFrame({
#     'player_number': player_nums,
#     'value': values,
#     'action_name': logs,
# })