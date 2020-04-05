#%%
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from os import getenv, mkdir, path
import pugsql
from random import choice
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
# noinspection PyPackageRequirements
from slack import WebClient
import pandas as pd
from pyvirtualdisplay import Display
import pickle
import re
import traceback


HOST = 'analytics.cnnfhkgooetn.us-west-2.rds.amazonaws.com'
PORT = '3306'
USER = getenv('MARIA_USER')
PASS = getenv('MARIA_PASS')


def engine_builder(engine=True):
    db_connect_url = URL(
        drivername='mysql',
        username=USER,
        password=PASS,
        host=HOST,
        port=PORT,
    )
    if engine:
        return create_engine(db_connect_url)
    else:
        return db_connect_url


def slack_message(body: str, channel: str):
    sc = WebClient(getenv('SLACK_TOKEN'))
    sc.chat_postMessage(
        channel=channel,
        text=body,
        username='StoneAge')


#%%
class StoneAge:
    def __init__(self, browser):
        """
        Initialized class variables, including existing game list from stored file.

        :param browser: Selenium webdriver object
        """
        self.browser = browser
        self.browser.get('https://en.boardgamearena.com')
        self.wait = WebDriverWait(self.browser, 30)

        # Validates the existence of the YAML file location.
        if not path.exists('data/'):
            mkdir('data')
        # If an existing list of games is present, read it in. Else, create an empty file.
        self.game_ids = set()

    def login(self):
        """
        Checks if user is logged in, and if not, go to login page and enter credentials.
        """
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
        """
        Goes to recent results page and extracts the game IDs from the top list.
        Checks for uniqueness against loaded YAML file, and subtracts game IDs existing in the DB.
        """
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

    def game_results(self, url: str):
        """
        Converts the table in the results screen to a dictionary.
        :param url: Formatted url for the game summary
        :return: Dictionary of the results table
        """
        self.browser.get(url)
        self.wait.until(EC.presence_of_element_located((By.ID, 'player_stats_table')))
        results = {}

        # Creates object for the tables and individual rows.
        table = self.browser.find_element_by_id('player_stats_table')
        rows = table.find_elements_by_tag_name('tr')

        # For each row in the table, create a list of values and assign it to the
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

        # Pulls additional game info from other page sources, relative to the player info.
        panel = self.browser.find_element_by_id('game_result_panel')

        newranks = [x.text for x in panel.find_elements_by_class_name('gamerank_value')]
        results['new_rank'] = newranks

        winpoints = [x.text for x in panel.find_elements_by_class_name('winpoints')]
        # TODO: winpointsarena needs to be excluded
        results['winpoints'] = winpoints

        return results

    def game_logs(self, url: str):
        """
        :param url: Formatted url for the game replay
        :return: Raw list of actions taken during the game
        """
        self.browser.get(url)
        self.wait.until(EC.presence_of_element_located((By.ID, 'gamelogs')))

        game = self.browser.find_element_by_id('gamelogs')
        actions = game.find_elements_by_class_name('gamelogreview')
        actions = [x.text for x in actions]

        return actions

    def game_info(self, game_id: int):
        """
        Main function for transforming the game summary and logs. Also outputs to Database.
        :param game_id: Integer for the game ID, used for identification and for urls.
        """

        # Sends slack notification that job has begun; creates url Strings
        slack_message(f'Loading game ID {game_id}', 'scheduled-jobs')
        results_url = f'https://en.boardgamearena.com/#!table?table={game_id}'
        replay_url = f'https://en.boardgamearena.com/#!gamereview?table={game_id}'

        # Return and write locally the results data.
        summary_results = self.game_results(results_url)
        pickle.dump(summary_results, open('data/results.pkl', 'wb'))

        # Return and write locally the log data.
        logs = self.game_logs(replay_url)
        pickle.dump(logs, open('data/logs.pkl', 'wb'))

        # Assign player order by finding logs where the first player of each round switches
        player_order = list(set([x[0:x.find(' is')] for x in logs if 'is now first player' in x][0:4]))

        # For each log item, assign the player ID.
        player_nums = []
        for x in logs:
            # Abandoned games are discarded.
            if 'chose to abandon' in x.lower():
                self.game_ids.remove(game_id)
                slack_message('game abandoned', 'scheduled-jobs')
                return

            # These log items do not have any game impact and/or are not specific to a player
            elif 'end of the game' in x.lower():
                player_nums.append(-1)

            elif 'end of game' in x.lower():
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

        # Assigns player index/number in place of name in the logs.
        for player in player_order:
            logs = [x.replace(player, 'player') for x in logs]

        # For each log, replaces numbers with the character i,
        # and creates a separate field in the table structure to capture the value;
        # This normalizes player actions in the log table.
        values = [int((re.findall('\d+', x) or [-1])[0]) for x in logs]
        logs = [re.sub(r'\d', 'i', x) for x in logs]

        log_df = pd.DataFrame({
            'player_number': player_nums,
            'value': values,
            'action_name': logs,
        })

        # Sets flag for a new turn/round; and assigns a turn/round number using that flag.
        log_df.loc[log_df['action_name'] == 'player is now first player', 'new_turn'] = 1
        log_df['turn_number'] = log_df['new_turn'].fillna(0).cumsum()

        # Sets move number as an index starting at 1.
        log_df['move_number'] = log_df.index + 1

        log_df['game_id'] = game_id
        log_df = log_df.drop('new_turn', axis=1)

        # Creates empty lists of the appropriate length for missing data.
        for key in summary_results.keys():
            if len(summary_results[key]) == 0:
                summary_results[key] = [None] * len(summary_results['Player Names'])
            if ((len(summary_results[key]) == 2 * len(summary_results['Player Names'])
                 and key == 'winpoints')):
                summary_results[key] = [x.strip() for x in summary_results[key] if len(x) > 0]

        # Converts summary results into a DataFrame and does some string cleanup.
        summary_df = pd.DataFrame(summary_results)
        summary_df.columns = summary_df.columns.str.replace("'", "").str.lower().str.replace(' ', '_')
        summary_df['game_id'] = game_id
        summary_df['player_idx'] = [player_order.index(x) for x in summary_results['Player Names']]

        if (log_df.isnull().values.any()) or (summary_df.isnull().values.any()):
            slack_message(f'Missing data found in tables:\n{summary_df}', 'job-errors')
            return

        # Writes tables to the database.
        pugsql.get_modules().clear()
        queries = pugsql.module('sql/')
        queries.connect(engine_builder(engine=False))

        log_row_ct = queries.insert_logs(log_df.to_dict(orient='records'))
        summary_row_ct = queries.insert_summary(summary_df.to_dict(orient='records'))

        # Removes completed game ID from the list and writes local list of recent game IDs.
        self.game_ids.remove(game_id)

        slack_message(f'Loaded game ID {game_id}'
                      f'\n{summary_row_ct} rows added to summary'
                      f'\n{log_row_ct} rows added to logs',
                      'scheduled-jobs')


def main():
    # Instantiates the browser and logs in.
    if ENV != 'local':
        # Initializes the display for headless use.
        display = Display(visible=0, size=(1366, 768))
        display.start()
        b = StoneAge(webdriver.Firefox())
    else:
        b = StoneAge(webdriver.Firefox(executable_path='/usr/local/bin/geckodriver'))

    b.login()
    b.get_recent_game_ids()

    # Select one of the recent games at random and process it.
    b.game_info(choice(list(b.game_ids)))

    b.browser.close()


#%%
if __name__ == '__main__':
    ENV = 'cloud'

    try:
        main()
    except TimeoutException:
        slack_message('Time Out Issue on Site', 'scheduled_jobs')
    except Exception as e:
        slack_message(''.join(traceback.format_exception(type(e), e, e.__traceback__)), 'job-errors')

