import json, os, time, datetime, re, requests, requests_cache
from urllib.request import urlretrieve
from urllib.parse import quote_plus, unquote_plus
from comics.models import Arc, Character, Creator, Team, Publisher, Series, Issue, Settings
from .comicfilehandler import ComicFileHandler
from . import fnameparser, utils

class ComicImporter(object):

	#==================================================================================================

	def __init__(self):
		# Setup requests caching
		requests_cache.install_cache('./media/CACHE/comicvine-cache', expire_after=1800)
		requests_cache.core.remove_expired_responses()

		# Set basic reusable strings
		self.api_key = Settings.get_solo().api_key
		self.directory_path = 'files'
		
		# Set up files to store processed files and directories
		self.processed_files_file = os.path.join(self.directory_path, '.processed-files')
		if not os.path.isfile(self.processed_files_file):
			open(self.processed_files_file, 'w')
		self.processed_dirs_file = os.path.join(self.directory_path, '.processed-dirs')
		if not os.path.isfile(self.processed_dirs_file):
			open(self.processed_dirs_file, 'w')

		# API Strings
		self.baseurl = 'http://comicvine.gamespot.com/api/'
		self.imageurl = 'http://comicvine.gamespot.com/api/image/'
		self.base_params = { 'format': 'json', 'api_key': self.api_key }
		self.headers = { 'user-agent': 'tenma' }

		# API field strings
		self.arc_fields = 'deck,description,id,image,name,site_detail_url'
		self.character_fields = 'deck,description,id,image,name,site_detail_url'
		self.creator_fields = 'deck,description,id,image,name,site_detail_url'
		self.issue_fields = 'api_detail_url,character_credits,cover_date,deck,description,id,image,issue_number,name,person_credits,site_detail_url,story_arc_credits,team_credits,volume'
		self.publisher_fields = 'deck,description,id,image,name,site_detail_url'
		self.query_issue_fields ='cover_date,id,issue_number,name,volume'
		self.query_issue_limit = '100'
		self.query_series_fields = 'id,name,start_year'
		self.query_series_limit = '10'
		self.series_fields = 'api_detail_url,deck,description,id,name,publisher,site_detail_url,start_year'
		self.team_fields = 'characters,deck,description,id,image,name,site_detail_url'


	#==================================================================================================

	def import_comic_files(self):
		''' Main entry point to import comic files. '''

		for entry in os.scandir(self.directory_path):
			filepath = os.path.join(self.directory_path, entry.name)

			# If file, process issue. If directory, process series.
			if entry.is_file():
				# Check for unprocessed files
				with open(self.processed_files_file, "a") as pff:
					processed_files = set(line.strip() for line in open(self.processed_files_file))

					if entry.name not in processed_files:
						# Check comic file validity
						if utils.valid_comic_file(entry.name):
							# Attempt to find match
							cvid = ''
							if self.api_key != '':
								cvid = self._find_issue_match(entry.name)
							if cvid != '':
								# Process issue with ComicVine
								self._process_issue(entry.name, cvid)
								# Write to the .processed file
								pff.write("%s\n" % entry.name)
							else:
								# Process issue without ComicVine
								self._process_issue_without_cvid(filepath)
			elif entry.is_dir():
				# Attempt to find match
				with open(self.processed_dirs_file, "a") as pff:
					processed_dirs = set(line.strip() for line in open(self.processed_dirs_file))

					cvid = ''
					if self.api_key != '':
						if entry.name in processed_dirs:
							cvid = re.match('.*\s|\s([0-9]+)', processed_dirs)[1]
						else:
							cvid = self._find_series_match(entry.name)

					if cvid != '':
						# Process series with ComicVine
						if entry.name not in processed_dirs:
							self._process_series(entry.name, cvid)
						self._process_series_directory(filepath, cvid)
						# Write to the .processed file
						pff.write(entry.name + " | " + str(cvid) + "\n")
					else:
						# Process series without ComicVine
						self._process_series_without_cvid(entry.name)

	#==================================================================================================

	def reprocess_issue(self, issue_id):
		''' Reprocess an existing issue in the comics directories. '''

		processed_files = set(line.strip() for line in open(self.processed_files_file))

		issue = Issue.objects.get(id=issue_id)
		cvid = ''

		# Check if there's already a cvid.
		if issue.cvid and issue.cvid != '':
			cvid = issue.cvid
		else:
			# Attempt to find match
			if self._api_key != '':
				cvid = self._find_match(issue.file)
			else:
				cvid = ''

		# Update issue
		with open(self.processed_files_file, "a") as pff:
			if cvid != '':
				# Process the issue with ComicVine
				self._process_issue(issue.file, cvid)
				# Write to the .processed file
				if issue.file not in processed_files:
					pff.write("%s\n" % issue.file)
			else:
				self._reprocess_issue_without_cvid(issue.id)


	#==================================================================================================

	def _find_issue_match(self, filename):
		'''
		Try to find a match in ComicVine for an issue.

		Returns a ComicVine ID.
		'''

		# Initialize response
		cvid = ''

		# Attempt to extract series name, issue number, and year
		extracted = fnameparser.extract(filename)
		series_name = utils.remove_special_characters(extracted[0])
		series_name_url = quote_plus(series_name)
		issue_number = extracted[1]
		issue_year = extracted[2]

		# First check if there's already a series locally
		matching_series = Series.objects.filter(name=series_name)

		if matching_series:
			if not matching_series[0].cvid == '':
				cvid = self._find_match_with_series(matching_series[0].cvid, issue_number)
				if not cvid == '':
					return cvid

		# Query Parameters
		query_params = self.base_params
		query_params['resources'] = 'issue'
		query_params['field_list'] = self.query_issue_fields
		query_params['limit'] = self.query_issue_limit

		# Check for series name and issue number, or just series name
		if series_name and issue_number:
			query_params['query'] = series_name + ' ' + issue_number
			query_response = requests.get(
				self.baseurl + 'search', 
				params=query_params, 
				headers=self.headers
			).json()
		elif series_name:
			query_params['query'] = series_name_url
			query_response = requests.get(
				self.baseurl + 'search', 
				params=query_params, 
				headers=self.headers
			).json()

		best_option_list = []

		# Try to find the closest match.
		for issue in query_response['results']:
			item_year = datetime.date.today().year
			item_number = 1
			item_name = ''

			if 'cover_date' in issue:
				if issue['cover_date']:
					item_year = issue['cover_date'][0:4]
			if 'issue_number' in issue:
				if issue['issue_number']:
					item_number = issue['issue_number']
			if 'name' in issue['volume']:
				if issue['volume']['name']:
					item_name = issue['volume']['name']
					item_name = utils.remove_special_characters(item_name)

			if series_name and issue_number and issue_year:
				if item_name == series_name and item_number == issue_number and item_year == issue_year:
					best_option_list.insert(0, issue['id'])
					break
				elif item_name == series_name and item_number == issue_number:
					best_option_list.insert(0, issue['id'])
			elif series_name and issue_number:
				if item_name == series_name and item_number == issue_number:
					best_option_list.insert(0, issue['id'])

		return best_option_list[0] if best_option_list else ''


	#==================================================================================================

	def _find_series_match(self, dirname):
		'''
		Try to find a match in ComicVine for a series.

		Returns a ComicVine ID.
		'''

		# Initialize response
		cvid = ''

		# Attempt to extract series name, issue number, and year
		extracted = fnameparser.extract(dirname)
		series_name = utils.remove_special_characters(extracted[0])
		series_name_url = quote_plus(series_name)
		series_year = extracted[2]

		# First check if there's already a series locally
		if series_year != '':
			matching_series = Series.objects.filter(name=series_name, year=int(series_year))
		else:
			matching_series = Series.objects.filter(name=series_name)

		if matching_series:
			if not matching_series[0].cvid == '':
				return matching_series[0].cvid

		# Query Parameters
		query_params = self.base_params
		query_params['resources'] = 'volume'
		query_params['field_list'] = self.query_series_fields
		query_params['limit'] = self.query_series_limit

		# Check for series name and issue number, or just series name
		if series_name:
			query_params['query'] = series_name_url
			query_response = requests.get(
				self.baseurl + 'search', 
				params=query_params, 
				headers=self.headers
			).json()

		best_option_list = []

		# Try to find the closest match.
		for series in query_response['results']:
			item_year = datetime.date.today().year
			item_name = ''

			if 'name' in series:
				if series['name']:
					item_name = series['name']
					item_name = utils.remove_special_characters(item_name)
			if 'start_year' in series:
				if series['start_year']:
					item_year = series['start_year']

			if series_name and series_year:
				if item_name == series_name and item_year == series_year:
					best_option_list.insert(0, series['id'])
					break
			elif series_name:
				if item_name == series_name:
					best_option_list.insert(0, series['id'])

		return best_option_list[0] if best_option_list else ''


	#==================================================================================================

	def _find_match_with_series(self, series_cvid, issue_number):
		'''
		Try to retrieve a match based on an existing series name.
		
		Returns a ComicVine ID.
		'''

		issue_cvid = ''

		if issue_number:
			# Query Parameters
			query_params = self.base_params
			query_params['field_list'] = 'issues'

			# Attempt to find issue based on extracted Series Name and Issue Number
			query_response = requests.get(
				self.baseurl + 'volume/4050-' + str(series_cvid),
				params=query_params, 
				headers=self.headers,
			).json()

			# Try to find the closest match.
			for issue in query_response['results']['issues']:
				item_number = issue['issue_number'] if issue['issue_number'] else ''
				if item_number == issue_number:
					issue_cvid = issue['id']

		return issue_cvid


	#==================================================================================================

	def _process_issue_without_cvid(self, filepath):
		'''	Create an issue without a ComicVine ID.	'''

		# Make sure the issue hadn't already been added
		matching_issue = Issue.objects.filter(file=filepath)

		filename = os.path.basename(filepath)

		if not matching_issue:
			# 1. Attempt to extract series name, issue number, and year
			extracted = fnameparser.extract(filepath)
			series_name = extracted[0]
			issue_number = extracted[1]
			issue_year = extracted[2]

			# 2. Set Issue Information:
			issue = Issue()
			issue.file = filepath
			issue.number = issue_number if issue_number else 1
			issue.date = issue_year + '-01-01' if issue_year else datetime.date.today()

			cfh = ComicFileHandler()
			issue.cover = cfh.extract_cover(filepath)

			# 3. Set Series Information:
			matching_series = Series.objects.filter(name=series_name)

			if not matching_series:
				series = Series()
				series.name = series_name
				series.save()
				issue.series = series
			else:
				issue.series = matching_series[0]

			# 4. Save Issue.
			issue.save()
		else:
			self._reprocess_issue_without_cvid(matching_issue[0].id)


	#==================================================================================================

	def _reprocess_issue_without_cvid(self, issue_id):
		'''	Create an issue without a ComicVine ID.	'''

		# Make sure the issue exists
		issue = Issue.objects.get(id=issue_id)

		if issue:
			# 1. Attempt to extract series name, issue number, year and cover.
			extracted = fnameparser.extract(issue.file)
			series_name = extracted[0]
			issue_number = extracted[1]
			issue_year = extracted[2]

			cfh = ComicFileHandler()
			issue_cover = cfh.extract_cover(issue.file)

			# 2. Update Issue information:
			Issue.objects.filter(id=issue_id).update(
				number=issue_number if issue_number else 1,
				date=issue_year + '-01-01' if issue_year else datetime.date.today(),
				cover=issue_cover,
			)

			# 3. Update Series information:
			if Series.objects.get(id=issue.series.id):
				Series.objects.filter(id=issue.series.id).update(
					name=series_name,
				)
			else:
				series = Series()
				series.name = series_name
				series.save()
				issue.series = series
				issue.save()


	#==================================================================================================

	def _process_issue(self, filename, cvid):
		'''	Creates or updates metadata from ComicVine for an Issue. '''

		# 1. Make initial API call
		# Query Parameters
		issue_params = self.base_params
		issue_params['field_list'] = self.issue_fields

		response_issue = requests.get(
			self.baseurl + 'issue/4000-' + str(cvid),
			params=issue_params,
			headers=self.headers,
		).json()

		# 2. Set Series
		matching_series = Series.objects.filter(cvid=response_issue['results']['volume']['id'])

		if not matching_series:
			series = self._create_series(response_issue['results']['volume']['api_detail_url'])
		else:
			series = self._update_series(matching_series[0].id, response_issue['results']['volume']['api_detail_url'])

		# 3. Set Issue
		matching_issue = Issue.objects.filter(file=filename)

		if not matching_issue:		
			issue = self._create_issue(os.path.join(self.directory_path, filename), response_issue['results']['api_detail_url'], series.id)
		else:
			issue = self._update_issue(matching_issue[0].id, response_issue['results']['api_detail_url'], series.id)

		# 4. Set Publisher
		# Query Parameters
		series_params = self.base_params
		series_params['field_list'] = 'publisher'

		response_series = requests.get(
			response_issue['results']['volume']['api_detail_url'],
			params=series_params,
			headers=self.headers,
		).json()

		matching_publisher = Publisher.objects.filter(cvid=response_series['results']['publisher']['id'])

		if not matching_publisher:
			self._create_publisher(response_series['results']['publisher']['api_detail_url'], issue.series.id)
		else:
			self._update_publisher(matching_publisher[0].id, response_series['results']['publisher']['api_detail_url'], issue.series.id)

		# 5. Set Arcs
		for story_arc in response_issue['results']['story_arc_credits']:
			matching_arc = Arc.objects.filter(cvid=story_arc['id'])
			if not matching_arc:
				self._create_arc(story_arc['api_detail_url'], issue.id)
			else:
				issue.arcs.add(self._update_arc(matching_arc[0].id, story_arc['api_detail_url']))

		# 6. Set Characters
		for character in response_issue['results']['character_credits']:
			matching_character = Character.objects.filter(cvid=character['id'])
			if not matching_character:
				self._create_character(character['api_detail_url'], issue.id)
			else:
				issue.characters.add(self._update_character(matching_character[0].id, character['api_detail_url']))

		# 7. Set Creators
		for person in response_issue['results']['person_credits']:
			matching_creator = Creator.objects.filter(cvid=person['id'])
			if not matching_creator:
				self._create_creator(person['api_detail_url'], issue.id)
			else:
				issue.creators.add(self._update_creator(matching_creator[0].id, person['api_detail_url']))

		# 8. Set Teams
		for team in response_issue['results']['team_credits']:
			matching_team = Team.objects.filter(cvid=team['id'])
			if not matching_team:
				self._create_team(team['api_detail_url'], issue.id)
			else:
				issue.teams.add(self._update_team(matching_team[0].id, team['api_detail_url']))


	#==================================================================================================
	def _process_series(self, series_directory, cvid):
		'''	Creates or updates metadata from ComicVine for a Series. '''

		# 1. Make initial API call
		# Query Parameters
		series_params = self.base_params
		series_params['field_list'] = self.series_fields

		response_series = requests.get(
			self.baseurl + 'volume/4050-' + str(cvid),
			params=series_params,
			headers=self.headers,
		).json()

		# 2. Set Series
		matching_series = Series.objects.filter(cvid=response_series['results']['id'])

		if not matching_series:
			series = self._create_series(response_series['results']['api_detail_url'])
		else:
			series = self._update_series(matching_series[0].id, response_series['results']['api_detail_url'])

		# 3. Set Publisher
		matching_publisher = Publisher.objects.filter(cvid=response_series['results']['publisher']['id'])

		if not matching_publisher:
			self._create_publisher(response_series['results']['publisher']['api_detail_url'], series.id)
		else:
			self._update_publisher(matching_publisher[0].id, response_series['results']['publisher']['api_detail_url'], series.id)


	#==================================================================================================
	def _process_series_directory(self, series_directory, series_cvid):
		''' Processes issues in a series directory '''

		series = Series.objects.get(cvid=series_cvid)

		for entry in os.scandir(series_directory):
			filepath = os.path.join(series_directory, entry.name)

			# If file, process issue. If directory, process series.
			if entry.is_file():
				# Check for unprocessed files
				with open(self.processed_files_file, "a") as pff:
					processed_files = set(line.strip() for line in open(self.processed_files_file))

					if entry.name not in processed_files:
						# Check comic file validity
						if utils.valid_comic_file(entry.name):
							# Attempt to find match
							cvid = ''

							# Attempt to extract series name, issue number, and year
							extracted = fnameparser.extract(entry.name)
							issue_number = extracted[1]

							if self.api_key != '':
								cvid = self._find_match_with_series(series_cvid, issue_number)
							if cvid != '':
								self._process_issue(filepath, cvid)
								pff.write("%s\n" % entry.name)
							else:
								# Process issue without ComicVine
								self._process_issue_without_cvid(filepath)
			elif entry.is_dir():
				# Attempt to find match
				with open(self.processed_dirs_file, "a") as pff:
					processed_dirs = set(line.strip() for line in open(self.processed_dirs_file))

					cvid = ''
					if self.api_key != '':
						if entry.name in processed_dirs:
							cvid = re.match('.*\s|\s([0-9]+)', processed_dirs)[1]
						else:
							cvid = self._find_series_match(entry.name)

					if cvid != '':
						# Process series with ComicVine
						if entry.name not in processed_dirs:
							self._process_series(entry.name, cvid)
						self._process_series_directory(filepath)
						# Write to the .processed file
						pff.write(entry.name + " | " + str(cvid) + "\n")
					else:
						# Process series without ComicVine
						self._process_series_without_cvid(entry.name)


	#==================================================================================================

	def _get_object_data(self, response):
		''' 
		Gathers object data from a response and tests each value to make sure 
		it exists in the response before trying to set it. 

		CVID and CVURL will always exist in a ComicVine response, so there
		is no need to verify this data.

		Returns a dictionary with all the gathered data.
		'''
		
		# Get Name
		name = ''

		if 'name' in response:
			if response['name']:
				name = response['name']

		# Get Start Year (only exists for Series objects)
		year = ''

		if 'start_year' in response:
			if response['start_year']:
				year = response['start_year']

		# Get Number (only exists for Issue objects)
		number = ''

		if 'issue_number' in response:
			if response['issue_number']:
				number = response['issue_number']

		# Get Date (only exists for Issue objects)
		date = datetime.date.today()

		if 'cover_date' in response:
			if response['cover_date']:
				date = response['cover_date']


		# Get Description (Favor short description if available)
		desc = ''

		if 'deck' in response:
			if response['deck']:
				desc = response['deck']
		if desc == '':
			if 'description' in response:
				if response['description']:
					desc = utils.parse_CV_HTML(response['description'])

		# Get Image
		image = ''

		if 'image' in response:
			if response['image']:
				image_url = self.imageurl + response['image']['super_url'].rsplit('/', 1)[-1]
				image_filename = unquote_plus(image_url.split('/')[-1])
				if image_filename != '1-male-good-large.jpg' and not re.match(".*question_mark_large.*.jpg", image_filename):
					image = utils.test_image(urlretrieve(image_url, 'media/images/' + image_filename)[0])

		# Create data object
		data = {
			'cvid': response['id'],  				# Always exists
			'cvurl': response['site_detail_url'],  	# Always exists
			'name': name,
			'year': year,
			'number': number,
			'date': date,
			'desc': desc,
			'image': image,
		}

		return data


	#==================================================================================================

	def _create_arc(self, api_url, issue_id):
		''' 
		Creates Arc from ComicVine API URL and adds it to
		it's corresponding Issue.

		Returns the Arc object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.arc_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		issue = Issue.objects.get(id=issue_id)

		# Create Arc
		a = issue.arcs.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return a


	#==================================================================================================

	def _create_character(self, api_url, issue_id):
		''' 
		Creates Character from ComicVine API URL and adds it to
		it's corresponding Issue.

		Returns the Character object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.character_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		issue = Issue.objects.get(id=issue_id)

		# Create Character
		ch = issue.characters.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return ch


	#==================================================================================================

	def _create_creator(self, api_url, issue_id):
		''' 
		Creates Creator from ComicVine API URL and adds it to
		it's corresponding Issue.

		Returns the Creator object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.creator_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		issue = Issue.objects.get(id=issue_id)

		# Create Creator
		cr = issue.creators.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return cr


	#==================================================================================================

	def _create_issue(self, file, api_url, series_id):
		''' 
		Creates Issue from ComicVine API URL and adds the 
		corresponding Series.

		Returns the Issue object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.issue_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		series = Series.objects.get(id=series_id)

		# Create Issue
		i = Issue.objects.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			number=data['number'],
			date=data['date'],
			file=file,
			series=series,
			cover=data['image'],
		)

		return i


	#==================================================================================================

	def _create_publisher(self, api_url, series_id):
		''' 
		Creates Publisher from ComicVine API URL and adds it to
		it's corresponding Series.

		Returns the Publisher object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.publisher_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Create Publisher
		p = Publisher.objects.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			logo=data['image'],
		)

		# Add Publisher to Series
		series = Series.objects.get(id=series_id)
		series.publisher = p
		series.save()

		return p


	#==================================================================================================

	def _create_team(self, api_url, issue_id):
		''' 
		Creates Team from ComicVine API URL and adds it to
		it's corresponding Issue.

		Returns the Team object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.team_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		issue = Issue.objects.get(id=issue_id)

		# Create Team
		t = issue.teams.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		# Add existing Characters to Team
		for character in response['results']['characters']:
			matching_character = Character.objects.filter(cvid=character['id'])
			if matching_character:
				team_item = Team.objects.filter(cvid=t.cvid)
				matching_character[0].teams.add(team_item[0])

		return t


	#==================================================================================================

	def _create_series(self, api_url):
		''' 
		Creates Series from ComicVine API URL.

		Returns the Series object created.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.series_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Create Series
		s = Series.objects.create(
			cvid=data['cvid'],
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			year=data['year'],
		)

		return s


	#==================================================================================================

	def _update_arc(self, obj_id, api_url):
		''' 
		Updates Arc from ComicVine API URL.

		Returns the Arc object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.arc_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Arc
		Arc.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return Arc.objects.get(id=obj_id)


	#==================================================================================================

	def _update_character(self, obj_id, api_url):
		''' 
		Updates Character from ComicVine API URL.

		Returns the Character object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.character_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Character
		Character.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return Character.objects.get(id=obj_id)


	#==================================================================================================

	def _update_creator(self, obj_id, api_url):
		''' 
		Updates Creator from ComicVine API URL.

		Returns the Creator object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.creator_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Creator
		Creator.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return Creator.objects.get(id=obj_id)


	#==================================================================================================

	def _update_issue(self, obj_id, api_url, series_id):
		''' 
		Updates Issue from ComicVine API URL.

		Returns the Issue object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.issue_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		issue =Issue.objects.get(id=obj_id)
		self._reset_issue(issue.id)

		series = Series.objects.get(id=series_id)

		# Update Issue
		Issue.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			number=data['number'],
			date=data['date'],
			series=series,
			cover=data['image'],
		)

		return Issue.objects.get(id=obj_id)


	#==================================================================================================

	def _update_publisher(self, obj_id, api_url, series_id):
		''' 
		Updates Publisher from ComicVine API URL.

		Returns the Publisher object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.publisher_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Publisher
		Publisher.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			logo=data['image'],
		)

		# Add Publisher to Series
		series = Series.objects.get(id=series_id)
		series.publisher = Publisher.objects.get(id=obj_id)
		series.save()

		return Publisher.objects.get(id=obj_id)


	#==================================================================================================

	def _update_team(self, obj_id, api_url):
		''' 
		Updates Team from ComicVine API URL.

		Returns the Team object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.team_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Team
		Team.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			image=data['image'],
		)

		return Team.objects.get(id=obj_id)


	#==================================================================================================

	def _update_series(self, obj_id, api_url):
		''' 
		Updates Series from ComicVine API URL.

		Returns the Series object udpated.
		'''

		# Request and Response
		params = self.base_params
		params['field_list'] = self.series_fields

		response = requests.get(
			api_url,
			params=params,
			headers=self.headers,
		).json()

		data = self._get_object_data(response['results'])

		# Update Series
		Series.objects.filter(id=obj_id).update(
			cvurl=data['cvurl'],
			name=data['name'],
			desc=data['desc'],
			year=data['year'],
		)

		return Series.objects.get(id=obj_id)

	#==================================================================================================

	def _reset_issue(self, obj_id):
		''' 
		Resets an Issue's fields.

		Returns the Issue object that was reset.
		'''
		issue = Issue.objects.get(id=obj_id)

		issue.cvurl = ''
		issue.name = ''
		issue.number = 1
		issue.date = datetime.date.today()
		issue.desc = ''
		issue.arcs.clear()
		issue.characters.clear()
		issue.creators.clear()
		issue.teams.clear()
		issue.cover = ''

		issue.save()

		return Issue.objects.get(id=obj_id)