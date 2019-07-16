#%%
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from os import getenv, mkdir, path
from random import choice
from sqlalchemy import create_engine, engine
from slack import WebClient
import pandas as pd
from pyvirtualdisplay import Display
import pickle
import re
import traceback
import yaml


HOST = 'analytics.cnnfhkgooetn.us-west-2.rds.amazonaws.com'
PORT = '3306'
USER = getenv('MARIA_USER')
PASS = getenv('MARIA_PASS')


def engine_builder():
    db_connect_url = engine.url.URL(
        drivername='mysql',
        username=USER,
        password=PASS,
        host=HOST,
        port=PORT,
    )
    return create_engine(db_connect_url)


def slack_message(body):
    sc = WebClient(getenv('SLACK_TOKEN'))
    sc.chat_postMessage(
        channel='scheduled-jobs',
        text=body,
        username='StoneAge')


#%%
class StoneAge:
    def __init__(self, browser):
        self.browser = browser
        self.browser.get('https://en.boardgamearena.com')
        self.wait = WebDriverWait(self.browser, 30)

        if not path.exists('data/'):
            mkdir('data')

        try:
            self.read_unparsed_game_ids()
            if not self.game_ids:
                raise FileNotFoundError
        except FileNotFoundError:
            print('Existing Games ID File Not Found')
            self.game_ids = set()
            open('data/new_games.yaml', 'a').close()

    def login(self):
        if 'boardgamearena.com' not in self.browser.current_url:
            self.browser.get('https://en.boardgamearena.com')

        try:
            if not self.browser.find_element_by_id('connected_username').text:
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

            login = self.browser.find_element_by_id("login_button")
            login.click()
            self.wait.until(EC.staleness_of(login))
        else:
            print("Already logged in")

    def get_recent_game_ids(self):
        url = 'https://en.boardgamearena.com/#!gamepanel?game=stoneage&section=lastresults'
        self.browser.get(url)

        for game in self.browser.find_elements_by_class_name('gamename'):
            s = game.find_element_by_xpath('..').get_property('href')
            print(s)
            s = s[s.find('table=') + 6:]
            try:
                self.game_ids.add(int(s))
            except ValueError:
                pass

        loaded = set(pd.read_sql('select distinct game_id from bgg.game_summary',
                                 engine_builder())['game_id'])
        self.game_ids.difference(loaded)
        self.write_new_game_ids()

    def write_new_game_ids(self):
        with open('data/new_games.yaml', 'w') as outfile:
            yaml.dump(self.game_ids, outfile)

    def read_unparsed_game_ids(self):
        with open("data/new_games.yaml", 'r') as stream:
            self.game_ids = yaml.load(stream)

    def game_results(self, url):
        self.browser.get(url)
        self.wait.until(EC.presence_of_element_located((By.ID, 'player_stats_table')))

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
        self.browser.get(url)
        self.wait.until(EC.presence_of_element_located((By.ID, 'gamelogs')))

        game = self.browser.find_element_by_id('gamelogs')
        actions = game.find_elements_by_class_name('gamelogreview')
        actions = [x.text for x in actions]

        return actions

    def game_info(self, game_id):
        slack_message(f'Loading game ID {game_id}')
        results_url = f'https://en.boardgamearena.com/#!table?table={game_id}'
        replay_url = f'https://en.boardgamearena.com/#!gamereview?table={game_id}'

        summary_results = self.game_results(results_url)
        pickle.dump(summary_results, open('data/results.pkl', 'wb'))

        logs = self.game_logs(replay_url)
        pickle.dump(logs, open('data/logs.pkl', 'wb'))

        # Log Cleanup
        player_order = list(set([x[0:x.find(' is')] for x in logs if 'is now first player' in x][0:4]))

        player_nums = []
        for x in logs:
            if 'chose to abandon' in x.lower():
                self.game_ids.remove(game_id)
                return

            elif 'end of the game' in x.lower():
                player_nums.append(-1)

            elif 'rematch' in x.lower():
                player_nums.append(-1)

            elif 'colors of' in x.lower():
                player_nums.append(-1)

            else:
                if 'out of time' in x.lower():
                    x = x[0:x.find('out of time') + 11]

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

        for key in summary_results.keys():
            if len(summary_results[key]) == 0:
                summary_results[key] = [None] * len(summary_results['Player Names'])

        summary_df = pd.DataFrame(summary_results)
        summary_df.columns = summary_df.columns.str.replace("'", "").str.lower().str.replace(' ', '_')
        summary_df['game_id'] = game_id

        log_df.to_sql('game_logs', engine_builder(), schema='bgg', if_exists='append', index=False)
        summary_df.to_sql('game_summary', engine_builder(), schema='bgg', if_exists='append', index=False)

        self.game_ids.remove(game_id)
        self.write_new_game_ids()

        slack_message(f'loaded game ID {game_id}')

def main():
    display = Display(visible=0, size=(1366, 768))
    display.start()

    b = StoneAge(webdriver.Firefox())
    b.login()

    b.get_recent_game_ids()

    # playing around with rate limits, so using random for now.
    b.game_info(choice(list(b.game_ids)))

    # working_list = list(b.game_ids)[:]
    # for g_id in working_list:
    #     b.game_info(g_id)
    #     break

    b.browser.close()

#%%
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        slack_message(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

